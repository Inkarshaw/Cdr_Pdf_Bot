import os
import re
import logging
from io import BytesIO
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, ConversationHandler, filters
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

logging.basicConfig(level=logging.WARNING)\nlogging.getLogger("httpx").setLevel(logging.WARNING)\nlogging.getLogger("telegram").setLevel(logging.WARNING)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

STATION_STEP, CRIME, SECTION, FROM_DATE, TO_DATE, RELATION = range(6)

STATION = "G7 Chetpet PS (L&O)"
FROM_ADDRESS = "Inspector of Police,<br/>G7 Chetpet PS (L&O),<br/>Chetpet, Chennai - 31."
TO_ADDRESS = "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."

def identifier(text):
    v = re.sub(r"\s+", "", text or "")
    if re.fullmatch(r"\d{10}", v):
        return "Mobile", v
    if re.fullmatch(r"\d{15}", v):
        return "IMEI", v
    return None, None

def parse_date(text):
    t = (text or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return None

def build_pdf(d):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=25.4*mm, leftMargin=25.4*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal2", parent=styles["Normal"], fontName="Helvetica",
                            fontSize=11, leading=16, spaceAfter=3)
    right = ParagraphStyle("right", parent=normal, alignment=TA_RIGHT)
    story = []
    story.append(Paragraph("Date: " + datetime.now().strftime("%d/%m/%Y"), right))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("From", normal))
    story.append(Paragraph(d["from_address"], ParagraphStyle("indent", parent=normal, leftIndent=10*mm)))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("To", normal))
    story.append(Paragraph(d["to_address"], ParagraphStyle("indent2", parent=normal, leftIndent=10*mm)))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("Respected Sir,", normal))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("Sub: Request -- CDR and SDR of Cell number -- Regarding", ParagraphStyle("sub", parent=normal, leftIndent=10*mm)))
    story.append(Paragraph(f"Ref : Cr.No: {d['crime']} U/s {d['section']}", ParagraphStyle("ref", parent=normal, leftIndent=10*mm)))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "It is kindly submitted that the above reference cited, please provide the Call Detail Records of the following Cell number for investigation purpose.",
        normal))
    story.append(Spacer(1, 3*mm))

    data = [
        ["S.No.", "Mobile/IMEI Number", "From", "To", "Whose/How related"],
        ["1", d["number"], d["from_date"], d["to_date"], d["relation"]],
    ]
    widths = [13*mm, 53*mm, 24*mm, 24*mm, 45*mm]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.6, colors.black),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME", (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 7*mm))
    story.append(Paragraph(
        "1. The subscriber identity has been ascertained and it is ensured that the person in question is not someone whose call details are of a sensitive nature.",
        normal))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "2. The number is not subscribed in the name of a sitting MP/MLA.",
        normal))
    doc.build(story)
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "CDR PDF generator ready.\n\nSend a 10-digit mobile number or 15-digit IMEI.\nUse /cancel anytime."
    )
    return ConversationHandler.END

async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind, value = identifier(update.message.text)
    if not kind:
        await update.message.reply_text("Send a valid 10-digit mobile number or 15-digit IMEI.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data.update(kind=kind, number=value)
    await update.message.reply_text("Enter Crime Number with year (example: 43/2026):")
    return CRIME

async def station_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    station = update.message.text.strip()
    if station in STATIONS:
        from_address, to_address = STATIONS[station]
    elif station == "Other Police Station":
        await update.message.reply_text("Type the station name and full From address separated by |\nExample: X1 Example PS | Inspector of Police, X1 Example PS, Chennai")
        context.user_data["awaiting_custom_station"] = True
        return STATION_STEP
    elif context.user_data.get("awaiting_custom_station"):
        if "|" not in station:
            await update.message.reply_text("Use: Station Name | Full From address")
            return STATION_STEP
        name, addr = [x.strip() for x in station.split("|", 1)]
        station = name
        from_address = addr.replace(",", ",<br/>")
        to_address = "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."
        context.user_data.pop("awaiting_custom_station", None)
    else:
        await update.message.reply_text("Please select a station from the buttons.")
        return STATION_STEP
    context.user_data.update(station=station, from_address=from_address, to_address=to_address)
    await update.message.reply_text("Enter Crime Number with year (example: 43/2026):", reply_markup=ReplyKeyboardRemove())
    return CRIME

async def crime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    v = update.message.text.strip()
    if not re.fullmatch(r"\d{1,6}/\d{4}", v):
        await update.message.reply_text("Use format Cr.No/Year, for example: 43/2026")
        return CRIME
    context.user_data["crime"] = v
    await update.message.reply_text("Enter Section(s), for example: 194 BNSS:")
    return SECTION

async def section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["section"] = update.message.text.strip()
    await update.message.reply_text("Enter From Date (DD/MM/YYYY):")
    return FROM_DATE

async def from_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_date(update.message.text)
    if not d:
        await update.message.reply_text("Invalid date. Enter as DD/MM/YYYY.")
        return FROM_DATE
    context.user_data["from_date"] = d
    await update.message.reply_text("Enter To Date (DD/MM/YYYY), or type TILL:")
    return TO_DATE

async def to_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if t.upper() in ("TILL", "TILL DATE", "TODAY"):
        context.user_data["to_date"] = "Till Date"
    else:
        d = parse_date(t)
        if not d:
            await update.message.reply_text("Enter DD/MM/YYYY or type TILL.")
            return TO_DATE
        context.user_data["to_date"] = d
    await update.message.reply_text("Whose/How related? (example: Suspect / Victim / Witness):")
    return RELATION

async def relation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["relation"] = update.message.text.strip()
    pdf = build_pdf(context.user_data)
    name = f"CDR_Request_{context.user_data['number']}.pdf"
    await update.message.reply_document(document=pdf, filename=name,
        caption="PDF generated. This tool only prepares the document; it does not obtain or submit telecom records.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send a mobile number or IMEI to start again.")
    return ConversationHandler.END

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, begin)],
        states={
            CRIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, crime)],
            SECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, section)],
            FROM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, from_date)],
            TO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, to_date)],
            RELATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, relation)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
