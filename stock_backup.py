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
from datetime import date, datetime
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


def fetch_variants(token, snapshot_date):
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    cursor = None
    rows = []
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
        if (page_number + 1) % 50 == 0:
            print(f"Shopify inventory: fetched {len(rows)} variants so far", flush=True)
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


def email_backup(snapshot_date, rows, attachment, capture_started_at):
    email_from = setting("MAIL_FROM")
    email_to = setting("MAIL_TO")
    app_password = setting("MAIL_APP_PASSWORD").replace(" ", "")
    filename = f"LeatherCompany_Stock_{snapshot_date}.csv"
    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = f"Leather Company – Daily stock backup – {snapshot_date}"
    msg.set_content(
        f"Attached is the Leather Company stock snapshot for {snapshot_date}.\n\n"
        f"Variants: {len(rows)}\n"
        f"Quantity: Shopify available stock across all locations.\n"
        f"Actual capture started (UK): {capture_started_at.isoformat(timespec='seconds')}\n"
        f"File: {filename}\n\n"
        "This is an automated inventory export."
    )
    msg.add_attachment(attachment, maintype="text", subtype="csv", filename=filename)

    # Gmail supports SMTP_SSL (465) and STARTTLS (587). Retry only if the
    # connection fails before sending, to avoid duplicate deliveries.
    connection_options = ((465, "SSL"), (465, "SSL"), (587, "STARTTLS"), (587, "STARTTLS"))
    for attempt, (port, mode) in enumerate(connection_options, start=1):
        server = None
        stage = "connecting"
        print(f"Gmail SMTP: attempt {attempt}/4, {mode} port {port}", flush=True)
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(
                    "smtp.gmail.com", 465, timeout=30,
                    context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()

            stage = "authenticating"
            server.login(email_from, app_password)
            stage = "sending"
            print(f"Gmail SMTP: authenticated; sending {filename}", flush=True)
            refused = server.send_message(msg)
            if refused:
                # Do not retry: some recipients may have already received it.
                raise RuntimeError(
                    f"Gmail rejected {len(refused)} recipient(s). Check MAIL_TO."
                )
            print(f"Email accepted by Gmail: {filename}; {len(rows)} variants", flush=True)
            return
        except smtplib.SMTPAuthenticationError as exc:
            raise RuntimeError(
                f"Gmail authentication rejected (SMTP {exc.smtp_code}). "
                "Check MAIL_FROM and MAIL_APP_PASSWORD in GitHub Secrets."
            ) from None
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                TimeoutError, OSError) as exc:
            if stage == "sending":
                # The server might have accepted the email before dropping.
                # A retry might deliver duplicate attachments.
                raise RuntimeError(
                    f"Gmail SMTP connection dropped while sending ({type(exc).__name__}). "
                    "Delivery is uncertain; check Gmail Sent and the Make mailhook "
                    "before manually rerunning."
                ) from None
            print(
                f"Gmail SMTP: connection failed during {stage} ({type(exc).__name__}); "
                "trying another connection if available.", flush=True
            )
            if attempt == len(connection_options):
                raise RuntimeError(
                    "Gmail SMTP couldn't connect or authenticate reliably "
                    "on ports 465 or 587 after 4 attempts."
                ) from None
            time.sleep(min(attempt * 3, 9))
        finally:
            if server is not None:
                # Closing a successful SMTP transaction can itself raise
                # SMTPServerDisconnected if the server closed before QUIT.
                # That should not cause a false job failure after send_message.
                try:
                    server.quit()
                except (smtplib.SMTPException, OSError):
                    server.close()


def main():
    # Make supplies the UK trading date at 23:45. This protects the filename
    # if GitHub starts a little after midnight. Manual runs default to today.
    snapshot_date = os.environ.get("SNAPSHOT_DATE", "").strip()
    if snapshot_date:
        try:
            if date.fromisoformat(snapshot_date).isoformat() != snapshot_date:
                raise ValueError("Invalid date format")
        except ValueError:
            raise RuntimeError("SNAPSHOT_DATE must be YYYY-MM-DD") from None

    print("Connecting to Shopify...", flush=True)
    token = get_token()
    print("Shopify token obtained.", flush=True)
    capture_started_at = datetime.now(ZoneInfo("Europe/London"))
    if not snapshot_date:
        snapshot_date = capture_started_at.date().isoformat()
    print(f"UK capture started: {capture_started_at.isoformat(timespec='seconds')}")
    print(f"Trading date/filename label: {snapshot_date}")
    if snapshot_date != capture_started_at.date().isoformat():
        print("WARNING: Capture is taking place on a different UK date than "
              "the requested trading date. Review for forecasting accuracy.")
    snapshot_date, rows = fetch_variants(token, snapshot_date)
    print(f"Shopify export complete: {len(rows)} variants.", flush=True)
    csv_bytes = csv_attachment(rows)
    print(f"CSV generated: {len(csv_bytes)} bytes, SKU and Stock only.", flush=True)
    email_backup(snapshot_date, rows, csv_bytes, capture_started_at)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
