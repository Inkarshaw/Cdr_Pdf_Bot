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

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

STATION_STEP, CRIME, SECTION, FROM_DATE, TO_DATE, RELATION, ADD_MORE = range(7)

STATION = "G7 Chetpet PS (L&O)"
FROM_ADDRESS = "Inspector of Police,<br/>G7 Chetpet PS (L&O),<br/>Chetpet, Chennai - 31."
TO_ADDRESS = "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."

STATIONS = {
    "G7 Chetpet PS (L&O)": (
        "Inspector of Police,<br/>G7 Chetpet PS (L&O),<br/>Chetpet, Chennai - 31.",
        "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."
    ),
    "G5 Secretariat Colony PS": (
        "Inspector of Police,<br/>G5 Secretariat Colony PS,<br/>Chennai.",
        "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."
    ),
    "G3 Kilpauk PS (L&O)": (
        "Inspector of Police,<br/>G3 Kilpauk PS (L&O),<br/>Chennai.",
        "The Deputy Commissioner of Police,<br/>Kilpauk District,<br/>Chennai - 600010."
    ),
}

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

    items = d.get("items") or [{
        "number": d["number"],
        "from_date": d["from_date"],
        "to_date": d["to_date"],
        "relation": d["relation"],
    }]
    data = [["S.No.", "Mobile/IMEI Number", "From", "To", "Whose/How related"]]
    for i, item in enumerate(items, 1):
        data.append([
            str(i), item["number"], item["from_date"], item["to_date"], item["relation"]
        ])
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
        "Enter Police Station code (example: G7):",
        reply_markup=ReplyKeyboardRemove()
    )
    return STATION_STEP

async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind, value = identifier(update.message.text)
    if not kind:
        await update.message.reply_text("Send a valid 10-digit mobile number or 15-digit IMEI.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data.update(kind=kind, number=value, station=STATION, from_address=FROM_ADDRESS, to_address=TO_ADDRESS)
    await update.message.reply_text("Enter Crime Number with year (example: 43/2026):")
    return CRIME

async def station_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # After station is resolved, accept the mobile number / IMEI.
    if context.user_data.get("station"):
        kind, value = identifier(text)
        if not kind:
            await update.message.reply_text("Send a valid 10-digit mobile number or 15-digit IMEI.")
            return STATION_STEP
        context.user_data.update(kind=kind, number=value)
        await update.message.reply_text("Enter Crime Number with year (example: 43/2026):")
        return CRIME

    code = re.sub(r"[^A-Z0-9]", "", text.upper())
    aliases = {
        "G7": "G7 Chetpet PS (L&O)",
        "G7CHETPET": "G7 Chetpet PS (L&O)",
        "G7CHETPETPS": "G7 Chetpet PS (L&O)",
        "G5": "G5 Secretariat Colony PS",
        "G5SECRETARIATCOLONY": "G5 Secretariat Colony PS",
        "G5SECRETARIATCOLONYPS": "G5 Secretariat Colony PS",
        "G3": "G3 Kilpauk PS (L&O)",
        "G3KILPAUK": "G3 Kilpauk PS (L&O)",
        "G3KILPAUKPS": "G3 Kilpauk PS (L&O)",
    }
    station = aliases.get(code)
    if not station:
        await update.message.reply_text("Station not recognised. Type G7, G5, or G3.")
        return STATION_STEP

    from_address, to_address = STATIONS[station]
    context.user_data.update(
        station=station,
        from_address=from_address,
        to_address=to_address
    )
    await update.message.reply_text(
        f"Selected: {station}\n\nSend a 10-digit mobile number or 15-digit IMEI:",
        reply_markup=ReplyKeyboardRemove()
    )
    return STATION_STEP

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
    item = {
        "number": context.user_data["number"],
        "from_date": context.user_data["from_date"],
        "to_date": context.user_data["to_date"],
        "relation": context.user_data["relation"],
    }
    context.user_data.setdefault("items", []).append(item)
    await update.message.reply_text(
        f"Added {context.user_data['number']}.\n\nAdd another mobile/IMEI with the same Crime No., Section and Police Station? Type YES or NO."
    )
    return ADD_MORE

async def add_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().upper()
    if answer in ("YES", "Y"):
        await update.message.reply_text(
            "Send the next 10-digit mobile number or 15-digit IMEI:"
        )
        return ADD_MORE
    if answer in ("NO", "N", "DONE"):
        context.user_data.setdefault("station", STATION)
        context.user_data.setdefault("from_address", FROM_ADDRESS)
        context.user_data.setdefault("to_address", TO_ADDRESS)
        pdf = build_pdf(context.user_data)
        name = f"CDR_Request_{context.user_data['crime'].replace('/', '_')}.pdf"
        await update.message.reply_document(
            document=pdf,
            filename=name,
            caption="PDF generated. To add another number to this same request, send /add."
        )
        saved = {
            "station": context.user_data.get("station", STATION),
            "from_address": context.user_data.get("from_address", FROM_ADDRESS),
            "to_address": context.user_data.get("to_address", TO_ADDRESS),
            "crime": context.user_data["crime"],
            "section": context.user_data["section"],
            "items": list(context.user_data.get("items", [])),
        }
        context.user_data.clear()
        context.user_data["last_request"] = saved
        return ConversationHandler.END

    kind, value = identifier(update.message.text)
    if kind:
        context.user_data.update(kind=kind, number=value)
        await update.message.reply_text(
            "Enter From Date for this number (DD/MM/YYYY):"
        )
        context.user_data["adding_number"] = True
        return FROM_DATE

    await update.message.reply_text("Type YES to add another number, or NO to generate the PDF.")
    return ADD_MORE

async def add_after_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved = context.user_data.get("last_request")
    if not saved:
        await update.message.reply_text("No previous PDF request is available. Send /start to create a new request.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data.update(saved)
    await update.message.reply_text(
        f"Adding to Cr.No. {saved['crime']} U/s {saved['section']}.\n\nSend the next 10-digit mobile number or 15-digit IMEI:"
    )
    return ADD_MORE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send a mobile number or IMEI to start again.")
    return ConversationHandler.END

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("add", add_after_pdf), MessageHandler(filters.TEXT & ~filters.COMMAND, begin)],
        states={
            STATION_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, station_step)],
            CRIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, crime)],
            SECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, section)],
            FROM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, from_date)],
            TO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, to_date)],
            RELATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, relation)],
            ADD_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_more)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
