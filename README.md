# Kleinanzeigen Watcher (Python + Google Sheets + GitHub Actions)

Regularly searches **Kleinanzeigen.de** for products listed in a **Google Sheet** and writes new results into a second worksheet.

## Features
- Global **max radius** (km) and **fetch frequency** (informational) from the `Config` tab.
- Per search: query, location/ZIP, min/max price; for `type=vehicle` optionally `km_min`/`km_max`.
- Results are written into the **`Results`** tab of the same sheet (duplicates are skipped).
- Uses a dedicated `LocationIDs` tab to ensure searches are restricted to the correct city/region.
- Runs automatically via **GitHub Actions** (daily; cron can be customized).

---

## Google Sheet Structure

### Tab `Config`
| key            | value |
|----------------|-------|
| max_radius_km  | 25    |
| fetch_frequency| daily |

### Tab `Searches`
| active | query     | location | price_min | price_max | type    | km_min | km_max |
|--------|-----------|----------|-----------|-----------|---------|--------|--------|
| TRUE   | iPhone 13 | Berlin   | 200       | 800       | generic |        |        |
| TRUE   | VW Golf   | München  | 3000      | 10000     | vehicle | 40000  | 140000 |

### Tab `LocationIDs`
To make sure the search really stays limited to your city + radius, you need to provide the correct **location IDs** (`lXXXX` codes from Kleinanzeigen URLs).

| city     | location_id |
|----------|-------------|
| Berlin   | 3331        |
| Hamburg  | 2760        |
| Garching | 6303        |
| München  | 6411        |
| Augsburg | 7518        |

- **city**: must match the `location` column in the `Searches` tab (case-insensitive).  
- **location_id**: the numeric part after `lXXXX` in a Kleinanzeigen URL. Example:  
  - Search “Berlin” manually on Kleinanzeigen → URL contains `.../k0l3331r25...` → `3331` is the ID for Berlin.  
- If no ID is provided, the script falls back to a slug-only search (`s-berlin`), which may show results from all of Germany.  
- For accurate searches you should maintain this tab with the IDs you need.

### Tab `Results` (written by the script)
| ad_id | query | title | price_eur | km | location | url | posted_at | fetched_at |
|-------:|--------|----------|----------:|----------:|----------|-------:|-------:|-------:|


---

## Google Service Account

1. In Google Cloud Console, create a **Service Account**.  
2. Generate a **JSON key** (you’ll need this JSON as a GitHub Secret).  
3. Share your **Google Sheet** with the **Service Account email** as **Editor**.  
4. The **Sheet ID** is in the URL:  
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit...`

---

## Local Testing

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export SHEET_ID="YOUR_SHEET_ID"
export GOOGLE_APPLICATION_CREDENTIALS_JSON='{"type":"service_account", ... }'

python kleinanzeigen_watcher.py