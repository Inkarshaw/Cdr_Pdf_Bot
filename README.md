# Cdr_Pdf_Bot

Private Telegram document-generation bot intended for the owner's authorized administrative workflow.

## Telegram
Send a 10-digit mobile number or a 15-digit IMEI. The bot validates the identifier, generates a PDF, and returns it in Telegram.

## Railway
Set this environment variable in Railway:

`TELEGRAM_BOT_TOKEN`

Never commit the Telegram bot token to GitHub.

Railway start command:

`python bot.py`

## Template
The current PDF is a starter layout. Replace `make_pdf()` with the approved request-document template before operational use.
