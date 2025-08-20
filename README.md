# Éxito Price & Seller Scraper

This repository contains a script to keep a Google Sheets tab up to date
with the latest price and seller information for products on
Éxito/VTEX.  Because Éxito hides price data from simple bots,
Playwright is used to render each product page like a real browser.

## Quick Start

1. **Create a Google Service Account.**
   - Enable the **Google Sheets API** for your project.
   - Download the JSON key.
   - Share the target spreadsheet with the service account email
     (`…@…iam.gserviceaccount.com`) and give it edit access.

2. **Prepare your spreadsheet.**
   - Create or open your Google Sheets file.
   - Name the tab/pestaña (sheet) e.g. `EXITO PRODUCTOS GPT`.
   - Add a header row with at least these columns (case‑insensitive):
     `URL`, `VENDEDOR`, `PRECIO VEI`.
   - Paste the product URLs in the `URL` column, one per row.  The
     script will fill in the other two columns.

3. **Create a GitHub repository.**  Add the files from this project.

4. **Define secrets in your repository (Settings → Secrets → Actions).**
   - `GOOGLE_CREDENTIALS_JSON` – copy the full contents of your
     service account JSON key and paste it here.
   - `SPREADSHEET_NAME` – the exact name of your Google Sheets file.
   - `SHEET_NAME` – the name of the tab (e.g. `EXITO PRODUCTOS GPT`).

5. **Push the code to GitHub.**  The included GitHub Actions
   workflow (`scrape.yml`) will run hourly between 08:00 and 17:00
   (Colombia time) from Monday through Saturday.  It launches
   Playwright, fetches price and seller, and writes results back to
   your sheet.  If you need to run it manually you can use the
   *Run workflow* button in the Actions tab.

## How It Works

The heart of the scraper is `exito_scraper.py`.  It:

1. Loads configuration from environment variables.
2. Opens the specified worksheet via the Google Sheets API.
3. Maintains a cursor in cell `A100000` so that each run starts
   where the previous left off.  When the end of the URL list is
   reached, it wraps back to row 2.
4. For each batch of URLs it launches a headless Chromium with
   Playwright, navigates to the page and attempts to extract price and
   seller using visible elements, schema.org JSON‑LD, or
   `dataLayer` pushes as a fallback.
5. Writes the results back to the `VENDEDOR` and `PRECIO VEI` columns.
6. Optionally writes a timestamp to a column named `ACTUALIZADO` if
   present.

The script enforces that it only runs during allowed hours (08:00–17:00
Colombia time) and days (Monday–Saturday).  Outside of that window
it immediately exits without making any changes.

If you wish to run the scraper locally, install the dependencies
(`pip install playwright gspread oauth2client pandas`) and run
`playwright install --with-deps chromium` once.  Then set the
environment variables as described and execute:

```
python exito_scraper.py
```

## Troubleshooting

- **Prices still show “NO DISPONIBLE”** – Ensure that the URLs point
  directly to product pages.  Some marketplace listings may rely on
  JavaScript widgets or block headless browsers; try adjusting
  selectors or increasing wait times.
- **Authentication errors** – Check that you copied the service account
  JSON correctly and that the sheet is shared with the service account.
- **Permission errors updating the sheet** – The service account must
  have edit permissions on the spreadsheet.

Feel free to adjust the `BATCH_SIZE` and schedule to suit your needs.
