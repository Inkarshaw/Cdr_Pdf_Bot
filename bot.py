import os
import re
import logging
from io import BytesIO

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

def classify_identifier(text: str):
    value = re.sub(r"\s+", "", text)
    if re.fullmatch(r"\d{10}", value):
        return "Mobile Number", value
    if re.fullmatch(r"\d{15}", value):
        return "IMEI", value
    return None, None

def make_pdf(kind: str, value: str) -> BytesIO:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    c.setTitle("Request PDF")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 90, "REQUEST DOCUMENT")
    c.setFont("Helvetica", 11)
    c.drawString(72, height - 125, f"{kind}: {value}")
    c.drawString(72, height - 150, "Generated for the user's authorized administrative workflow.")
    c.drawString(72, height - 190, "Note: Replace this starter layout with the approved request template.")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a 10-digit mobile number or a 15-digit IMEI. "
        "I will generate the request PDF."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind, value = classify_identifier(update.message.text or "")
    if not kind:
        await update.message.reply_text(
            "Please send only a 10-digit mobile number or 15-digit IMEI."
        )
        return
    pdf = make_pdf(kind, value)
    filename = f"request_{value}.pdf"
    await update.message.reply_document(document=pdf, filename=filename)

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
