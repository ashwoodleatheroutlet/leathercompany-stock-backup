# Leather Company — Daily Shopify Stock CSV by Email

This solution **does not use Google Apps Script, Google Drive or Google OAuth**. GitHub Actions runs the Python script each night and emails the CSV using your personal Gmail account's *app password* via Gmail SMTP.

## What it backs up

- **Only** Leather Company (`fonzleatherstyles.myshopify.com`)
- Every Shopify variant, including zero-stock variants
- Date, product title, variant title, SKU, barcode, product status, inventory tracking, *total available quantity* across Shopify locations, and Shopify variant ID
- One dated CSV email per successful day at approximately **23:30 Europe/London** (GitHub scheduled workflows can be delayed)
- One CSV attachment per email, keeping history in your inbox

This is a snapshot of Shopify **available** inventory, not a physical on-hand count or a per-location breakdown.

## Setup — you do not need to change your Shopify app

1. Create a **private** repository at https://github.com/new, e.g. `leathercompany-stock-backup`.
2. Upload both `stock_backup.py` and `.github/workflows/daily-stock.yml` preserving the nested workflow folder path. The ZIP contains both files in the correct structure.
3. In your Google account, enable 2-Step Verification if necessary, then open https://myaccount.google.com/apppasswords. Create an app password named `Leather Company Stock Backup`. **This is a separate 16-character app password, not your normal Gmail password.** Availability depends on your account security configuration. If Google doesn't offer app passwords, do not disable Advanced Protection; use a different approved email-delivery provider instead.
4. GitHub repository > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**. Add the following **five** secrets:

| Secret | Value |
|---|---|
| `SHOPIFY_CLIENT_ID` | Client ID from your installed **Daily Stock Backup** app in Shopify Dev Dashboard |
| `SHOPIFY_CLIENT_SECRET` | Client Secret from that app (never paste in issues or chat) |
| `MAIL_FROM` | Your personal Gmail address (the account that generated the app password) |
| `MAIL_APP_PASSWORD` | The 16-character Gmail app password |
| `MAIL_TO` | Email address to receive CSV (can be the same Gmail address) |

5. Open the repository's **Actions** tab and enable workflows if prompted. Select **Leather Company daily stock CSV email** > **Run workflow** > **Run workflow**. Check the run log and your mailbox (including Spam).
6. Once the manual run succeeds, the workflow will automatically run every day at around 23:30 UK time. GitHub can delay scheduled runs. **Keep the repo active** and periodically verify daily emails; GitHub can disable scheduled workflows in inactive repositories.

## Troubleshooting

- `shop_not_permitted`: Shopify app and live store might not be under the same Shopify organisation. Recheck in Dev Dashboard.
- HTTP 401/403: Shopify credentials, scopes, or installation issue.
- `SMTPAuthenticationError`: ensure Gmail 2-Step Verification and a valid **app password**, not your regular account password. If app passwords aren't offered, this method isn't available on that account.
- No email: inspect GitHub Actions job logs and Spam folder; check `MAIL_TO`.
- API throttle: script retries temporary errors; persistent failures exit without emailing an incomplete CSV.

**Security:** Keep the GitHub repository private, never write or commit secrets into files, and rotate both Shopify and Gmail app passwords if exposed. The script needs Shopify's read-only permissions, not write access. Never store live inventory or credentials in repository logs or public GitHub issues.

## Limitations

- GitHub scheduled workflows are **best effort**, not guaranteed to start exactly at 23:30.
- Gmail SMTP app passwords are less secure than modern OAuth integrations; keep this credential in GitHub encrypted secrets, not in code, and revoke it when no longer needed.
- No live Shopify/API or SMTP test has been performed, because your credentials aren't available.

## Official references

- Shopify client credentials: https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant
- Shopify product variants: https://shopify.dev/docs/api/admin-graphql/2026-10/queries/productVariants
- GitHub scheduling: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule
- Google app passwords: https://support.google.com/accounts/answer/185833
