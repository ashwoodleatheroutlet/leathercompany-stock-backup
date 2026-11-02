"""Leather Company daily Shopify inventory CSV emailed via Gmail SMTP.

Runs without Google Apps Script or Google OAuth. Uses Python's standard library.
Requires a Shopify Dev Dashboard app installed on the same-org store and
read_products / read_inventory scopes.
"""

import csv
import io
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

SHOP = "fonzleatherstyles.myshopify.com"  # Leather Company only
API_VERSION = "2026-10"
PAGE_SIZE = 75
QUERY = """
query StockSnapshot($cursor: String) {
  productVariants(first: 75, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      sku
      barcode
      title
      inventoryQuantity
      inventoryItem { tracked }
      product { title status }
    }
  }
}
"""

def setting(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment secret: {name}")
    return value


def post_json_or_form(url, payload, headers, *, is_json, retries=6):
    data = (json.dumps(payload).encode("utf-8") if is_json else
            urllib.parse.urlencode(payload).encode("utf-8"))
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                status = resp.status
                body = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 15))
                continue
            raise RuntimeError(f"Connection to Shopify failed: {type(e).__name__}") from e
        try:
            response = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError(f"Shopify returned HTTP {status} with invalid JSON") from None
        if status == 429 or status in (500, 502, 503, 504) or (
            isinstance(response, dict) and any(
                (e.get("extensions") or {}).get("code") == "THROTTLED"
                for e in response.get("errors", [])
            )
        ):
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 20))
                continue
        if status < 200 or status >= 300:
            # Do not log request headers, client secrets or access tokens.
            raise RuntimeError(
                f"Shopify HTTP {status}: {response.get('error_description') or response.get('error') or 'request failed'}"
            )
        return response
    raise RuntimeError("Shopify request failed after multiple retries")


def get_token():
    result = post_json_or_form(
        f"https://{SHOP}/admin/oauth/access_token",
        {"grant_type": "client_credentials", "client_id": setting("SHOPIFY_CLIENT_ID"),
         "client_secret": setting("SHOPIFY_CLIENT_SECRET")},
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        is_json=False,
    )
    if not result.get("access_token"):
        raise RuntimeError("Shopify didn't return an access token. Check app installation/organisation.")
    return result["access_token"]


def fetch_variants(token):
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    cursor = None
    rows = []
    snapshot_date = datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    for page_number in range(2000):
        result = post_json_or_form(
            url, {"query": QUERY, "variables": {"cursor": cursor}},
            {"Content-Type": "application/json", "Accept": "application/json",
             "X-Shopify-Access-Token": token},
            is_json=True,
        )
        if result.get("errors"):
            message = "; ".join(e.get("message", "unknown GraphQL error") for e in result["errors"])
            raise RuntimeError(f"Shopify GraphQL error: {message}")
        try:
            connection = result["data"]["productVariants"]
            variants = connection["nodes"]
            page_info = connection["pageInfo"]
        except (KeyError, TypeError):
            raise RuntimeError("Unexpected Shopify response. No partial backup will be emailed.") from None
        for variant in variants:
            product = variant.get("product") or {}
            inventory_item = variant.get("inventoryItem") or {}
            rows.append({
                "snapshot_date": snapshot_date,
                "product_title": product.get("title") or "",
                "variant_title": variant.get("title") or "",
                "sku": variant.get("sku") or "",
                "barcode": variant.get("barcode") or "",
                "product_status": product.get("status") or "",
                "inventory_tracked": "Yes" if inventory_item.get("tracked") else "No",
                "available_quantity": ("" if variant.get("inventoryQuantity") is None
                                       else variant["inventoryQuantity"]),
                "variant_id": variant.get("id") or "",
            })
        if not page_info.get("hasNextPage"):
            if not rows:
                raise RuntimeError("Shopify returned no product variants. Email cancelled.")
            return snapshot_date, rows
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("Missing Shopify pagination cursor. Email cancelled.")
    raise RuntimeError("Variant pagination exceeded 2000 pages. Email cancelled.")


def csv_attachment(rows):
    # Keep only the two requested columns in the emailed file.
    # Preserve SKU as text, including leading zeros. Do not aggregate variants.
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(("SKU", "Stock"))
    writer.writerows((row["sku"], row["available_quantity"]) for row in rows)
    # UTF-8 BOM helps Excel open the CSV consistently.
    csv_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
    if len(csv_bytes) > 20 * 1024 * 1024:
        raise RuntimeError("CSV is larger than 20MB; email not sent.")
    return csv_bytes


def email_backup(snapshot_date, rows, attachment):
    email_from = setting("MAIL_FROM")
    email_to = setting("MAIL_TO")
    app_password = setting("MAIL_APP_PASSWORD")
    filename = f"LeatherCompany_Stock_{snapshot_date}.csv"
    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = f"Leather Company – Daily stock backup – {snapshot_date}"
    msg.set_content(
        f"Attached is the Leather Company stock snapshot for {snapshot_date}.\n\n"
        f"Variants: {len(rows)}\n"
        f"Quantity: Shopify available stock across all locations.\n"
        f"File: {filename}\n\n"
        "This is an automated inventory export."
    )
    msg.add_attachment(attachment, maintype="text", subtype="csv", filename=filename)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=45, context=ssl.create_default_context()) as server:
        server.login(email_from, app_password.replace(" ", ""))
        server.send_message(msg)
    print(f"Sent {filename} to {email_to}: {len(rows)} variants.")


def main():
    token = get_token()
    snapshot_date, rows = fetch_variants(token)
    csv_bytes = csv_attachment(rows)
    email_backup(snapshot_date, rows, csv_bytes)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
