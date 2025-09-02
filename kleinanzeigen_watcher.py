import os, re, time, json
from datetime import datetime, timezone
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ---------- Google Sheets ----------
SHEET_ID = os.getenv("SHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if not SHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise SystemExit("Env missing: SHEET_ID and GOOGLE_APPLICATION_CREDENTIALS_JSON are required.")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS = Credentials.from_service_account_info(json.loads(SERVICE_ACCOUNT_JSON), scopes=SCOPES)
SHEETS = build("sheets", "v4", credentials=CREDS).spreadsheets()

CONFIG_TAB = "Config"
SEARCHES_TAB = "Searches"
RESULTS_TAB = "Results"
LOCATIONS_TAB = "LocationIDs"

# ---------- Kleinanzeigen ----------
BASE_HOST = "https://www.kleinanzeigen.de"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (+https://github.com/your-org/kleinanzeigen-watcher)",
    "Accept-Language": "de-DE,de;q=0.9",
}

AD_CARD_SELECTOR = "article.aditem"
TITLE_SELECTOR = ".aditem-main--middle--title a"
PRICE_SELECTOR = ".aditem-main--middle--price-shipping .aditem-main--middle--price"
META_SELECTOR = ".aditem-main--top .aditem-main--top--left"

KM_REGEX = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{1,6})\s*km", re.IGNORECASE)

# ---------- Google Sheets helpers ----------
def read_sheet_range(tab, rng="A:Z"):
    res = SHEETS.values().get(spreadsheetId=SHEET_ID, range=f"{tab}!{rng}").execute()
    return res.get("values", [])

def write_rows_append(tab, rows):
    if not rows:
        return
    body = {"values": rows}
    SHEETS.values().append(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()

def ensure_headers(tab, headers):
    existing = read_sheet_range(tab, "A1:Z1")
    if existing and existing[0] == headers:
        return
    SHEETS.values().clear(spreadsheetId=SHEET_ID, range=f"{tab}!A:Z").execute()
    SHEETS.values().update(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()

def get_config():
    cfg_rows = read_sheet_range(CONFIG_TAB, "A:B")
    cfg = {}
    for r in cfg_rows:
        if not r or not r[0].strip():
            continue
        key = r[0].strip()
        val = r[1].strip() if len(r) > 1 else ""
        cfg[key] = val
    max_radius = int(cfg.get("max_radius_km", "25"))
    frequency = cfg.get("fetch_frequency", "daily")
    return max_radius, frequency

# ---------- Location IDs ----------
def normalize_city(s: str) -> str:
    return (s or "").strip().lower()

def load_location_ids():
    rows = read_sheet_range(LOCATIONS_TAB, "A:B")
    loc_map = {}
    if not rows:
        return loc_map
    start_idx = 1 if rows and rows[0] and normalize_city(rows[0][0]) in ("city", "stadt") else 0
    for r in rows[start_idx:]:
        if len(r) < 2:
            continue
        city = normalize_city(r[0])
        loc_id = re.sub(r"\D", "", r[1]) if r[1] else ""
        if city and loc_id:
            loc_map[city] = loc_id
    return loc_map

# ---------- URL builder ----------
def build_search_url(query, location_str, radius_km, price_min, price_max, loc_id=None):
    q = quote_plus(query or "")
    slug = (location_str or "").strip().lower().replace(" ", "-") or "deutschland"
    base = f"{BASE_HOST}/s-{slug}/{q}/k0"
    if loc_id:
        base += f"l{loc_id}"
    if radius_km:
        base += f"r{int(radius_km)}"
    if price_min is not None or price_max is not None:
        lo = str(price_min) if price_min is not None else ""
        hi = str(price_max) if price_max is not None else ""
        base += f"p{lo}p{hi}"
    return base

# ---------- Parsing ----------
def parse_price_eur(text: str):
    if not text:
        return None
    t = text.lower()
    if "verschenken" in t:
        return 0
    raw = text.replace("\xa0", " ").strip()
    # match integer + optional decimal part
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[,\.]\d{1,2})?", raw)
    if not m:
        return None
    num = m.group(1).replace(".", "").replace(" ", "")
    try:
        return int(num)
    except ValueError:
        return None

def extract_km(text_blob):
    if not text_blob:
        return None
    m = KM_REGEX.search(text_blob.replace("\xa0", " "))
    return int(m.group(1).replace(".", "").replace(" ", "")) if m else None

def ad_id_from_url(url):
    m = re.search(r"/(\d{6,})-", url)
    return m.group(1) if m else url

def fetch_listings(search_url):
    r = requests.get(search_url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # Broader, resilient selectors (match current site markup)
    cards = soup.select("article.aditem, li.ad-listitem, div.aditem")

    items = []
    for c in cards:
        a = c.select_one(
            ".aditem-main--middle--title a, a.ellipsis, a.ellipsis-text"
        )
        if not a or not a.get("href"):
            continue

        url = a["href"]
        if url.startswith("/"):
            url = BASE_HOST + url
        title = a.get_text(strip=True)

        price_el = c.select_one(
            ".aditem-main--middle--price-shipping .aditem-main--middle--price, "
            ".aditem-main--middle--price, .aditem-price"
        )
        price_eur = parse_price_eur(price_el.get_text(strip=True) if price_el else "")

        meta_el = c.select_one(
            ".aditem-main--top .aditem-main--top--left, .aditem-main--top"
        )
        meta_text = meta_el.get_text(" ", strip=True) if meta_el else ""

        km = extract_km(c.get_text(" ", strip=True))

        items.append({
            "ad_id": ad_id_from_url(url),
            "title": title,
            "price_eur": price_eur,
            "km": km,
            "meta": meta_text,
            "url": url,
        })

    return items

def load_existing_ad_ids():
    rows = read_sheet_range(RESULTS_TAB, "A2:A")
    return {r[0] for r in rows if r}

# ---------- Main ----------
def main():
    ensure_headers(RESULTS_TAB, ["ad_id", "query", "title", "price_eur", "km", "location", "url", "posted_at", "fetched_at"])

    max_radius, _ = get_config()
    city_to_id = load_location_ids()

    searches = read_sheet_range(SEARCHES_TAB)
    if not searches or len(searches) < 2:
        print("No searches found.")
        return

    header = [h.lower() for h in searches[0]]
    idx = {h: i for i, h in enumerate(header)}
    required = ["active", "query", "location", "price_min", "price_max", "type", "km_min", "km_max"]
    for r in required:
        if r not in idx:
            raise RuntimeError(f"Missing column in Searches: {r}")
    has_loc_id_col = "location_id" in idx

    existing_ids = load_existing_ad_ids()
    to_append = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in searches[1:]:
        row += [""] * (len(header) - len(row))

        active_val = str(row[idx["active"]]).strip().lower()
        active = active_val in ("true", "1", "yes", "y", "ja")
        if not active:
            continue

        query = str(row[idx["query"]]).strip()
        location = str(row[idx["location"]]).strip()
        price_min = int(str(row[idx["price_min"]]).replace("_","")) if str(row[idx["price_min"]]).strip() else None
        price_max = int(str(row[idx["price_max"]]).replace("_","")) if str(row[idx["price_max"]]).strip() else None
        kind = (str(row[idx["type"]]).strip().lower() or "generic")

        km_min = km_max = None
        if kind == "vehicle":
            km_min = int(str(row[idx["km_min"]]).replace("_","")) if str(row[idx["km_min"]]).strip() else None
            km_max = int(str(row[idx["km_max"]]).replace("_","")) if str(row[idx["km_max"]]).strip() else None

        loc_id = None
        if has_loc_id_col:
            raw = str(row[idx["location_id"]]).strip()
            loc_id = re.sub(r"\D", "", raw) if raw else None
        if not loc_id:
            loc_id = city_to_id.get(normalize_city(location))

        url = build_search_url(query, location, max_radius, price_min, price_max, loc_id=loc_id)

        try:
            items = fetch_listings(url)
        except Exception as e:
            print(f"Fetch failed for {query} @ {location}: {e}")
            time.sleep(2)
            continue

        for it in items:
            if kind == "vehicle":
                if km_min is not None and (it["km"] is None or it["km"] < km_min):
                    continue
                if km_max is not None and (it["km"] is None or it["km"] > km_max):
                    continue

            if it["ad_id"] in existing_ids:
                continue

            posted_at = ""
            loc_in_meta = ""
            if it["meta"]:
                parts = [p.strip() for p in it["meta"].split("•")]
                if len(parts) == 2:
                    posted_at, loc_in_meta = parts
                elif len(parts) == 1:
                    loc_in_meta = parts[0]

            to_append.append([
                it["ad_id"], query, it["title"],
                it["price_eur"] if it["price_eur"] is not None else "",
                it["km"] if it["km"] is not None else "",
                loc_in_meta, it["url"], posted_at, now_iso
            ])
            existing_ids.add(it["ad_id"])

        time.sleep(1.2)

    if to_append:
        write_rows_append(RESULTS_TAB, to_append)
        print(f"Added {len(to_append)} new rows.")
    else:
        print("No new results.")

if __name__ == "__main__":
    main()