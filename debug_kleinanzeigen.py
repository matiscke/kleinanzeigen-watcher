# file: debug_kleinanzeigen.py
import os, re, json, sys
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SHEET_ID = os.getenv("SHEET_ID")
SA_JSON = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
SA_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")  # optional path fallback

if not SHEET_ID:
    print("Missing SHEET_ID"); sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def load_creds():
    if SA_JSON:
        try:
            info = json.loads(SA_JSON)
            return Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            print("Failed to parse GOOGLE_APPLICATION_CREDENTIALS_JSON. "
                  "Make sure it is compact one-line JSON and quoted. Error:", e)
    if SA_FILE:
        try:
            return Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
        except Exception as e:
            print("Failed to read GOOGLE_APPLICATION_CREDENTIALS file:", SA_FILE, "Error:", e)
    print("Missing valid Google credentials. Set GOOGLE_APPLICATION_CREDENTIALS_JSON or GOOGLE_APPLICATION_CREDENTIALS.")
    sys.exit(1)

CREDS = load_creds()
SHEETS = build("sheets", "v4", credentials=CREDS).spreadsheets()

CONFIG_TAB = "Config"
SEARCHES_TAB = "Searches"
LOCATIONS_TAB = "LocationIDs"

BASE_HOST = "https://www.kleinanzeigen.de"
HEADERS = {"User-Agent": "Mozilla/5.0 (+debug run)", "Accept-Language": "de-DE,de;q=0.9"}

def read(tab, rng="A:Z"):
    return SHEETS.values().get(spreadsheetId=SHEET_ID, range=f"{tab}!{rng}").execute().get("values", [])

def normalize_city(s):
    return (s or "").strip().lower()

def load_config():
    rows = read(CONFIG_TAB, "A:B")
    cfg = {r[0].strip(): (r[1].strip() if len(r) > 1 else "") for r in rows if r and r[0].strip()}
    return int(cfg.get("max_radius_km", "25") or "25")

def load_location_ids():
    rows = read(LOCATIONS_TAB, "A:B")
    m = {}
    if not rows:
        return m
    start = 1 if rows[0] and normalize_city(rows[0][0]) in ("city","stadt") else 0
    for r in rows[start:]:
        if len(r) < 2:
            continue
        city = normalize_city(r[0])
        loc_id = re.sub(r"\D", "", r[1]) if r[1] else ""
        if city and loc_id:
            m[city] = loc_id
    return m

def build_url(query, location_str, radius_km, price_min, price_max, loc_id=None, alt_price=False):
    q = quote_plus(query or "")
    slug = (location_str or "").strip().lower().replace(" ", "-") or "deutschland"
    u = f"{BASE_HOST}/s-{slug}/{q}/k0"
    if loc_id:
        u += f"l{loc_id}"
    if radius_km:
        u += f"r{int(radius_km)}"
    if price_min is not None or price_max is not None:
        lo = str(price_min) if price_min is not None else ""
        hi = str(price_max) if price_max is not None else ""
        u += f"p{lo}p{hi}" if not alt_price else (f"/preis:{lo}:{hi}" if (lo or hi) else "")
    return u

def looks_like_consent(html: str) -> bool:
    t = (html or "").lower()
    return ("einwilligung" in t and "cookies" in t) or ("cloudflare" in t and "attention required" in t)

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("article.aditem, li.ad-listitem, div.aditem")
    out = []
    for c in cards:
        a = c.select_one(".aditem-main--middle--title a, a.ellipsis, a.ellipsis-text")
        if not a or not a.get("href"):
            continue
        title = a.get_text(strip=True)
        href = a["href"]
        if href.startswith("/"):
            href = BASE_HOST + href
        out.append((title, href))
    return out

def main():
    max_radius = load_config()
    city_map = load_location_ids()

    searches = read(SEARCHES_TAB)
    if not searches or len(searches) < 2:
        print("No searches in sheet."); return

    header = [h.lower() for h in searches[0]]
    idx = {h: i for i, h in enumerate(header)}
    for col in ["active","query","location","price_min","price_max"]:
        if col not in idx:
            print("Missing column:", col); return

    print("=== DEBUG RUN ===")
    for row in searches[1:]:
        row += [""] * (len(header) - len(row))
        active = str(row[idx["active"]]).strip().lower() in ("true","1","yes","ja","y")
        if not active:
            continue

        query = str(row[idx["query"]]).strip()
        location = str(row[idx["location"]]).strip()
        price_min = int(str(row[idx["price_min"]]).replace("_","")) if str(row[idx["price_min"]]).strip() else None
        price_max = int(str(row[idx["price_max"]]).replace("_","")) if str(row[idx["price_max"]]).strip() else None

        loc_id = city_map.get(normalize_city(location))
        url = build_url(query, location, max_radius, price_min, price_max, loc_id=loc_id, alt_price=False)
        print("\nCity:", location, "| ID:", loc_id or "-", "\nURL:", url)

        try:
            html = fetch(url)
        except Exception as e:
            print("Fetch error:", e); continue

        if looks_like_consent(html):
            print("⚠️ Consent/anti-bot page detected. Try local run or self-hosted runner."); continue

        cards = parse_cards(html)
        print("Parsed cards:", len(cards))
        for t, h in cards[:5]:
            print(" -", t[:100], "→", h)

        if len(cards) == 0:
            alt = build_url(query, location, max_radius, price_min, price_max, loc_id=loc_id, alt_price=True)
            print("Trying ALT URL:", alt)
            try:
                html2 = fetch(alt)
                if not looks_like_consent(html2):
                    cards2 = parse_cards(html2)
                    print("Parsed cards (ALT):", len(cards2))
                    for t, h in cards2[:5]:
                        print(" -", t[:100], "→", h)
            except Exception as e:
                print("ALT fetch error:", e)

if __name__ == "__main__":
    main()