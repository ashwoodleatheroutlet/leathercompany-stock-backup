Leather Company daily stock backup -- SMTP connection fix

Replace ONLY the stock_backup.py file in your existing GitHub repository.
Keep .github/workflows/daily-stock.yml unchanged.
Keep all GitHub Secrets, both Make scenarios and Google Drive settings unchanged.

New logging:
* Shopify token obtained
* Shopify export complete: [count] variants
* CSV generated: [size] bytes
* Gmail SMTP: attempt N, SSL port 465 or STARTTLS port 587
* Gmail SMTP: authenticated; sending...
* Email accepted by Gmail ...

New reliability behaviour:
* Retries Gmail SMTP transport errors that occur BEFORE sending.
* Attempts SMTP SSL port 465, then STARTTLS port 587.
* Ignores a connection drop on QUIT after Gmail accepted the message.
* Does NOT automatically retry after transmission starts (to avoid duplicates).

Test by manually running the GitHub workflow; it will capture stock at the actual test time,
not recreate a previous day. Verify email and Drive receipt.
