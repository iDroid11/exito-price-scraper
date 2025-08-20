"""
exito_scraper.py
==================

This script is designed to run headlessly with Playwright to fetch the
latest price and seller information for products hosted on the Éxito
marketplace (a VTEX‑based site). The script connects to a Google
Spreadsheet, reads product URLs from a specified column and writes back
two columns: the seller and the current price.  It processes rows in
batches so that large sheets do not exceed execution limits, and it
rotates through the sheet on subsequent runs.  When scheduled via
GitHub Actions or another scheduler, it will keep the sheet up to date.

To use this script you need a Google service account with the Sheets API
enabled.  Share your spreadsheet with the service account’s email.

Environment variables
---------------------

Set the following environment variables for configuration:

```
SPREADSHEET_NAME   – Name of the Google spreadsheet file
SHEET_NAME         – Name of the sheet/tab (default: EXITO PRODUCTOS GPT)
GOOGLE_CREDENTIALS_JSON – Contents of the service account JSON key
BATCH_SIZE         – Number of rows to process per run (default: 100)
TIME_ZONE          – IANA time zone (default: America/Bogota)
```

The script writes the current cursor position into cell A100000 of the
sheet.  Do not use this cell for your data.

Run with:

```
python exito_scraper.py
```

When scheduling via GitHub Actions, define the environment variables in
the workflow and set GOOGLE_CREDENTIALS_JSON as a secret.
"""

import asyncio
import json
import os
import re
from datetime import datetime
from typing import List, Tuple

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.async_api import async_playwright



def _should_run_now(time_zone: str = "America/Bogota") -> bool:
    """Return True if current local time is within allowed window.

    We only run Monday–Saturday between 08:00 and 17:00 local time.
    The scheduler should also enforce this, but this function gives
    another layer of protection.

    Args:
        time_zone: IANA timezone string (default: America/Bogota).

    Returns:
        bool: True if within allowed hours, False otherwise.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # fallback if backport is installed

    now = datetime.now(ZoneInfo(time_zone))
    # weekday: Monday=0, Sunday=6
    if now.weekday() == 6:  # Sunday
        return False
    return 8 <= now.hour <= 17



def _open_sheet() -> gspread.Worksheet:
    """Authenticate and return the target worksheet.

    Environment variables must define SPREADSHEET_NAME, SHEET_NAME and
    GOOGLE_CREDENTIALS_JSON.

    Returns:
        Worksheet: gspread worksheet object.
    """
    spreadsheet_name = os.environ["SPREADSHEET_NAME"]
    sheet_name = os.environ.get("SHEET_NAME", "EXITO PRODUCTOS GPT")
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise RuntimeError("Environment variable GOOGLE_CREDENTIALS_JSON is missing")
    # load credentials from JSON string
    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(credentials)
    spreadsheet = client.open(spreadsheet_name)
    worksheet = spreadsheet.worksheet(sheet_name)
    return worksheet



def _read_batch(worksheet: gspread.Worksheet, batch_size: int) -> Tuple[int, List[str]]:
    """Read a batch of product URLs starting from the cursor.

    The cursor is stored in cell A100000. If empty, start at row 2.

    Args:
        worksheet: The worksheet to read from.
        batch_size: Number of rows to read.

    Returns:
        Tuple[int, List[str]]: (start_row, list of URLs)
    """
    try:
        cursor_val = worksheet.acell("A100000").value
        start_row = int(cursor_val) if cursor_val else 2
    except Exception:
        start_row = 2
    # Ensure start_row is at least 2
    if start_row < 2:
        start_row = 2

    # Read the entire column for URLs
    col_values = worksheet.col_values(1)  # We'll find URL header later; but first column not used.
    header = worksheet.row_values(1)
    # Determine URL column index (1-based)
    url_col_idx = None
    for idx, val in enumerate(header, start=1):
        if val.strip().upper() == "URL":
            url_col_idx = idx
            break
    if url_col_idx is None:
        raise RuntimeError("No 'URL' column found in header row")
    # Get data rows for URL column
    total_rows = worksheet.row_count
    end_row = min(start_row + batch_size - 1, total_rows)
    urls: List[str] = worksheet.get(f"{gspread.utils.rowcol_to_a1(start_row, url_col_idx)}:" f"{gspread.utils.rowcol_to_a1(end_row, url_col_idx)}")
    # Flatten list of lists
    urls_flat = [row[0] for row in urls]
    return start_row, urls_flat



def _write_results(
    worksheet: gspread.Worksheet,
    start_row: int,
    sellers: List[str],
    prices: List[str],
    time_zone: str = "America/Bogota",
) -> None:
    """Write seller and price results back to the sheet.

    Finds columns 'VENDEDOR' and 'PRECIO VEI' by header names. Optionally
    writes a timestamp to a column called 'ACTUALIZADO' if it exists.

    Args:
        worksheet: Target worksheet.
        start_row: The starting row for this batch.
        sellers: List of seller strings.
        prices: List of price strings.
        time_zone: IANA timezone for timestamps.
    """
    header = worksheet.row_values(1)
    col_seller = None
    col_price = None
    col_ts = None
    for idx, val in enumerate(header, start=1):
        name = val.strip().upper()
        if name == "VENDEDOR":
            col_seller = idx
        elif name == "PRECIO VEI":
            col_price = idx
        elif name == "ACTUALIZADO":
            col_ts = idx
    if col_seller is None or col_price is None:
        raise RuntimeError("Columns 'VENDEDOR' and/or 'PRECIO VEI' not found")
    # Build update ranges
    end_row = start_row + len(sellers) - 1
    seller_range = f"{gspread.utils.rowcol_to_a1(start_row, col_seller)}:" f"{gspread.utils.rowcol_to_a1(end_row, col_seller)}"
    price_range = f"{gspread.utils.rowcol_to_a1(start_row, col_price)}:" f"{gspread.utils.rowcol_to_a1(end_row, col_price)}"
    seller_cells = worksheet.range(seller_range)
    price_cells = worksheet.range(price_range)
    for idx, cell in enumerate(seller_cells):
        cell.value = sellers[idx]
    for idx, cell in enumerate(price_cells):
        cell.value = prices[idx]
    updates = seller_cells + price_cells
    # Timestamp column if present
    if col_ts is not None:
        ts_range = f"{gspread.utils.rowcol_to_a1(start_row, col_ts)}:" f"{gspread.utils.rowcol_to_a1(end_row, col_ts)}"
        ts_cells = worksheet.range(ts_range)
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # fallback
        now_str = datetime.now(ZoneInfo(time_zone)).strftime("%Y-%m-%d %H:%M:%S")
        for cell in ts_cells:
            cell.value = now_str
        updates += ts_cells
    worksheet.update_cells(updates, value_input_option="USER_ENTERED")



async def _extract_price_vendor(page, url: str) -> Tuple[str, str]:
    """Extract the price and vendor from a product page.

    Playwright opens the page, waits for network to settle, and tries
    multiple strategies to extract price and vendor:
    1. Inspecting visible elements that contain a currency and seller.
    2. Parsing JSON-LD blocks for schema.org Product objects.
    3. Parsing dataLayer pushes if present.

    Args:
        page: The Playwright Page object.
        url: URL to visit.

    Returns:
        Tuple[str, str]: (price, vendor) – returns 'NO DISPONIBLE' if not found.
    """
    price = ""
    vendor = ""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # 1) Try visible elements for price (contains digits and currency symbol)
        price_selectors = [
            "span[class*='price']",
            "div[class*='price'] span",
            "span[data-testid*='price']",
            "span:has-text('$')",
            "div:has-text('$')",
        ]
        for css in price_selectors:
            element = await page.query_selector(css)
            if element:
                text = (await element.inner_text()) or ""
                match = re.search(r"(\d[\d\.\,]+)", text)
                if match:
                    price = match.group(1)
                    break
        # 2) Vendor visible (e.g. 'Vendido por')
        vendor_selectors = [
            "text=Vendido por",
            "span:has-text('Vendido por')",
            "div:has-text('Vendido por')",
        ]
        for sel in vendor_selectors:
            element = await page.query_selector(sel)
            if element:
                text = (await element.inner_text()) or ""
                match = re.search(r"Vendido por[:\s]*([^\n\r]+)", text, re.IGNORECASE)
                if match:
                    vendor = match.group(1).strip()
                    break
        # 3) JSON-LD extraction
        if not price or not vendor:
            html = await page.content()
            # find all <script type="application/ld+json">
            for m in re.finditer(
                r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>", html, re.IGNORECASE
            ):
                try:
                    raw = m.group(1).strip()
                    data = json.loads(raw)
                    nodes = data if isinstance(data, list) else [data]
                    for node in nodes:
                        # unwrap @graph
                        subnodes = node.get("@graph") if isinstance(node, dict) else None
                        if subnodes and isinstance(subnodes, list):
                            nodes.extend(subnodes)
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        types = node.get("@type")
                        if not types:
                            continue
                        # unify to list
                        type_list = types if isinstance(types, list) else [types]
                        if "Product" not in type_list:
                            continue
                        offers = node.get("offers")
                        if isinstance(offers, list):
                            offers = offers[0]
                        if isinstance(offers, dict):
                            if not price:
                                p = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice") or ""
                                if p:
                                    price = str(p).strip()
                            if not vendor:
                                s = offers.get("seller")
                                if isinstance(s, dict):
                                    vendor = s.get("name") or s.get("@name") or ""
                        if not vendor and node.get("brand"):
                            brand = node.get("brand")
                            if isinstance(brand, dict):
                                vendor = brand.get("name") or ""
                            elif isinstance(brand, str):
                                vendor = brand
                        if price and vendor:
                            break
                    if price and vendor:
                        break
                except Exception:
                    continue
        # 4) dataLayer extraction
        if not price or not vendor:
            html = await page.content()
            for m in re.finditer(r"dataLayer\.push\((\{[\s\S]*?\})\);", html, re.IGNORECASE):
                try:
                    obj = json.loads(m.group(1))
                    if not price:
                        p = obj.get("price") or obj.get("productPrice") or ""
                        if p:
                            price = str(p)
                    if not vendor:
                        v = obj.get("seller") or obj.get("sellerName") or ""
                        if v:
                            vendor = v
                    if price and vendor:
                        break
                except Exception:
                    continue
    except Exception:
        pass
    # Normalize values
    price = price.strip() if price else "NO DISPONIBLE"
    vendor = vendor.strip() if vendor else "NO DISPONIBLE"
    # Prepend label
    if vendor != "NO DISPONIBLE" and not vendor.lower().startswith("vendido por"):
        vendor = f"Vendido por: {vendor}"
    return price, vendor



async def main() -> None:
    """Entry point for asynchronous execution."""
    # Ensure we only run within allowed time window
    tz = os.environ.get("TIME_ZONE", "America/Bogota")
    if not _should_run_now(tz):
        print("Outside permitted hours; skipping execution.")
        return
    batch_size = int(os.environ.get("BATCH_SIZE", "100"))
    worksheet = _open_sheet()
    start_row, urls = _read_batch(worksheet, batch_size)
    if not urls:
        # reset cursor and try again
        worksheet.update_acell("A100000", "2")
        start_row, urls = _read_batch(worksheet, batch_size)
        if not urls:
            print("No URLs found to process.")
            return
    sellers: List[str] = []
    prices: List[str] = []
    # Launch Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="es-CO",
        )
        page = await context.new_page()
        for url in urls:
            price, vendor = await _extract_price_vendor(page, url)
            prices.append(price)
            sellers.append(vendor)
        await context.close()
        await browser.close()
    # Write back to sheet
    _write_results(worksheet, start_row, sellers, prices, time_zone=tz)
    # Update cursor for next run
    next_row = start_row + len(urls)
    # If we've processed past the end, wrap around to row 2
    last_data_row = len(worksheet.get_all_values())
    if next_row > last_data_row:
        next_row = 2
    worksheet.update_acell("A100000", str(next_row))
    print(f"Processed rows {start_row} through {start_row + len(urls) - 1}.")



if __name__ == "__main__":
    asyncio.run(main())
