import os, re, time, json
from datetime import datetime, timezone
from urllib.parse import urlencode
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

# ---------- Kleinanzeigen ----------
BASE_SEARCH_URL = "https://www.kleinanzeigen.de/s-suche.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (+https://github.com/your-org/kleinanzeigen-watcher)",
    "Accept-Language": "de-DE,de;q=0.9",
}

AD_CARD_SELECTOR = "article.aditem"
TITLE_SELECTOR = ".aditem-main--middle--title a"
PRICE_SELECTOR = ".aditem-main--middle--price-shipping .aditem-main--middle--price"
META_SELECTOR = ".aditem-main--top .aditem-main--top--left"

KM_REGEX = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{1,6})\s*km", re.IGNORECASE)

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

def build_search_url(query, location, radius_km, price_min, price_max):
    params = {"keywords": query, "locationStr": location, "radius": radius_km}
    if price_min is not None or price_max is not None:
        lo = str(price_min) if price_min is not None else ""
        hi = str(price_max) if price_max is not None else ""
        params["price"] = f"{lo}-{hi}"
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"

def parse_price_eur(text):
    if not text:
        return None
    t = text.lower()
    if "verschenken" in t:
        return 0
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})*|\d+)", text.replace("\xa0", ""))
    return int(m.group(1).replace(".", "").replace(" ", "")) if m else None

def extract_km(text_blob):
    if not text_blob:
        return None
    m = KM_REGEX.search(text_blob.replace("\xa0", " "))
    return int(m.group(1).replace(".", "").replace(" ", "")) if m else None

def ad_id_from_url(url):
    m = re.search(r"/(\d{6,})-", url)
    return m.group(1) if m else url

def fetch_listings(search_url):
    r = requests.get(search_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select(AD_CARD_SELECTOR)
    items = []
    for c in cards:
        a = c.select_one(TITLE_SELECTOR)
        if not a or not a.get("href"):
            continue
        url = a["href"]
        if url.startswith("/"):
            url = "https://www.kleinanzeigen.de" + url
        title = a.get_text(strip=True)

        price_el = c.select_one(PRICE_SELECTOR)
        price_eur = parse_price_eur(price_el.get_text(strip=True) if price_el else "")

        meta_el = c.select_one(META_SELECTOR)
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

def main():
    ensure_headers(RESULTS_TAB, ["ad_id", "query", "title", "price_eur", "km", "location", "url", "posted_at", "fetched_at"])

    max_radius, _ = get_config()

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

    existing_ids = load_existing_ad_ids()
    to_append = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in searches[1:]:
        # Robust gegen fehlende/kurze Zeilen
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

        url = build_search_url(query, location, max_radius, price_min, price_max)
        try:
            items = fetch_listings(url)
        except Exception as e:
            print(f"Fetch failed for {query} @ {location}: {e}")
            time.sleep(3)
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

        time.sleep(1.5)  # freundlich bleiben

    if to_append:
        write_rows_append(RESULTS_TAB, to_append)
        print(f"Added {len(to_append)} new rows.")
    else:
        print("No new results.")

if __name__ == "__main__":
    main()
