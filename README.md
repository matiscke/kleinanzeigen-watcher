# Kleinanzeigen Watcher (Python + Google Sheets + GitHub Actions)

Regularly searches **Kleinanzeigen.de** for products listed in a **Google Sheet** and writes new results into a second worksheet.

## Features
- Global **max radius** (km) and **fetch frequency** (informational) from the `Config` tab.
- Per search: query, location/ZIP, min/max price; for `type=vehicle` optionally `km_min`/`km_max`.
- Results are written into the **`Results`** tab of the same sheet (duplicates are skipped).
- Runs automatically via **GitHub Actions** (daily; cron can be customized).

---

## Google Sheet Structure

### Tab `Config`
| key            | value |
|----------------|-------|
| max_radius_km  | 25    |
| fetch_frequency| daily |

### Tab `Searches`
| active | query               | location | price_min | price_max | type     | km_min | km_max |
|-------:|---------------------|----------|----------:|----------:|----------|-------:|-------:|
| TRUE   | Road Bike Carbon 54 | Berlin   | 300       | 1200      | generic  |        |        |
| TRUE   | VW Golf 7           | 10115    | 4000      | 11000     | vehicle  | 40000  | 140000 |

### Tab `Results` (written by the script)
| ad_id | query | title | price_eur | km | location | url | posted_at | fetched_at |

> Tip: Create these three tabs exactly as shown and add a few rows in `Searches` first.

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
