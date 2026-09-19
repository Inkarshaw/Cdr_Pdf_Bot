import os
import re
import json
import hmac
import base64
import hashlib
import logging
import threading
import uuid
from io import BytesIO
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

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

STATION_STEP, CRIME, SECTION, FROM_DATE, TO_DATE, RELATION, ADD_MORE, CHANGE_NUMBER, REMOVE_NUMBER = range(9)

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
        f"Selected: {station}\n\nEnter Crime Number with year (example: 43/2026):",
        reply_markup=ReplyKeyboardRemove()
    )
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
    await update.message.reply_text(
        "Send all mobile numbers / IMEIs in ONE message, one per line.\n"
        "You can add the relation after each number.\n\n"
        "Example:\n"
        "9876543210 Suspect\n"
        "9123456789 Victim\n"
        "123456789012345 Witness"
    )
    return RELATION

async def relation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [x.strip() for x in update.message.text.splitlines() if x.strip()]
    items = []
    invalid = []

    for line in lines:
        m = re.match(r"^(\d{10}|\d{15})(?:\s*[|,\-]\s*|\s+)?(.*)$", line)
        if not m:
            invalid.append(line)
            continue
        number = m.group(1)
        relation_text = m.group(2).strip() or "-"
        kind, value = identifier(number)
        if not kind:
            invalid.append(line)
            continue
        items.append({
            "number": value,
            "from_date": context.user_data["from_date"],
            "to_date": context.user_data["to_date"],
            "relation": relation_text,
        })

    if invalid or not items:
        msg = "I couldn't read these lines:\n" + "\n".join(invalid or lines)
        msg += "\n\nUse one per line, for example:\n9876543210 Suspect\n123456789012345 Witness"
        await update.message.reply_text(msg)
        return RELATION

    existing_items = list(context.user_data.get("items", []))
    items = existing_items + items
    context.user_data["items"] = items
    context.user_data["number"] = items[0]["number"]
    context.user_data["relation"] = items[0]["relation"]
    context.user_data.setdefault("station", STATION)
    context.user_data.setdefault("from_address", FROM_ADDRESS)
    context.user_data.setdefault("to_address", TO_ADDRESS)

    pdf = build_pdf(context.user_data)
    name = f"CDR_Request_{context.user_data['crime'].replace('/', '_')}.pdf"
    await update.message.reply_document(
        document=pdf,
        filename=name,
        caption=f"PDF generated with {len(items)} number(s). To add more later, send /add."
    )

    saved = {
        "station": context.user_data.get("station", STATION),
        "from_address": context.user_data.get("from_address", FROM_ADDRESS),
        "to_address": context.user_data.get("to_address", TO_ADDRESS),
        "crime": context.user_data["crime"],
        "section": context.user_data["section"],
        "from_date": context.user_data["from_date"],
        "to_date": context.user_data["to_date"],
        "items": list(items),
    }
    context.user_data.clear()
    context.user_data["last_request"] = saved
    return ConversationHandler.END

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
        f"Adding to Cr.No. {saved['crime']} U/s {saved['section']}.\n"
        f"Date range: {saved.get('from_date', '-')} to {saved.get('to_date', '-')}\n\n"
        "Send the additional number(s) in ONE message, one per line."
    )
    return RELATION

async def change_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved = context.user_data.get("last_request")
    if not saved or not saved.get("items"):
        await update.message.reply_text("No previous PDF request is available. Send /start to create one.")
        return ConversationHandler.END

    rows = []
    for i, item in enumerate(saved["items"], 1):
        rows.append(f"{i}. {item['number']} - {item.get('relation', '-')}")
    await update.message.reply_text(
        "Which number do you want to change?\n\n" +
        "\n".join(rows) +
        "\n\nSend: row number + new mobile/IMEI\nExample: 2 9876543210"
    )
    return CHANGE_NUMBER

async def change_number_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved = context.user_data.get("last_request")
    text = update.message.text.strip()
    m = re.fullmatch(r"(\d+)\s+(\d{10}|\d{15})", text)
    if not m:
        await update.message.reply_text("Use: row number + new mobile/IMEI\nExample: 2 9876543210")
        return CHANGE_NUMBER

    row = int(m.group(1))
    new_number = m.group(2)
    if row < 1 or row > len(saved["items"]):
        await update.message.reply_text(f"Choose a row from 1 to {len(saved['items'])}.")
        return CHANGE_NUMBER

    kind, value = identifier(new_number)
    if not kind:
        await update.message.reply_text("Enter a valid 10-digit mobile number or 15-digit IMEI.")
        return CHANGE_NUMBER

    old_number = saved["items"][row - 1]["number"]
    saved["items"][row - 1]["number"] = value
    context.user_data["last_request"] = saved

    pdf_data = dict(saved)
    pdf_data["number"] = saved["items"][0]["number"]
    pdf_data["relation"] = saved["items"][0].get("relation", "-")
    pdf_data["from_date"] = saved.get("from_date", saved["items"][0]["from_date"])
    pdf_data["to_date"] = saved.get("to_date", saved["items"][0]["to_date"])

    pdf = build_pdf(pdf_data)
    name = f"CDR_Request_{saved['crime'].replace('/', '_')}.pdf"
    await update.message.reply_document(
        document=pdf,
        filename=name,
        caption=f"Changed {old_number} to {value}. Updated PDF generated."
    )
    return ConversationHandler.END

async def remove_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved = context.user_data.get("last_request")
    if not saved or not saved.get("items"):
        await update.message.reply_text("No previous PDF request is available. Send /start to create one.")
        return ConversationHandler.END

    rows = [f"{i}. {item['number']} - {item.get('relation', '-')}" for i, item in enumerate(saved["items"], 1)]
    await update.message.reply_text(
        "Which number do you want to remove?\n\n" +
        "\n".join(rows) +
        "\n\nSend the row number only. Example: 2"
    )
    return REMOVE_NUMBER

async def remove_number_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved = context.user_data.get("last_request")
    text = update.message.text.strip()
    if not re.fullmatch(r"\d+", text):
        await update.message.reply_text("Send the row number only. Example: 2")
        return REMOVE_NUMBER

    row = int(text)
    if row < 1 or row > len(saved["items"]):
        await update.message.reply_text(f"Choose a row from 1 to {len(saved['items'])}.")
        return REMOVE_NUMBER
    if len(saved["items"]) == 1:
        await update.message.reply_text("This is the only number in the request. Use /start if you want to create a different request.")
        return ConversationHandler.END

    removed = saved["items"].pop(row - 1)
    context.user_data["last_request"] = saved

    pdf_data = dict(saved)
    pdf_data["number"] = saved["items"][0]["number"]
    pdf_data["relation"] = saved["items"][0].get("relation", "-")
    pdf_data["from_date"] = saved.get("from_date", saved["items"][0]["from_date"])
    pdf_data["to_date"] = saved.get("to_date", saved["items"][0]["to_date"])

    pdf = build_pdf(pdf_data)
    name = f"CDR_Request_{saved['crime'].replace('/', '_')}.pdf"
    await update.message.reply_document(
        document=pdf,
        filename=name,
        caption=f"Removed {removed['number']}. Updated PDF generated with {len(saved['items'])} number(s)."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# My Cases secure Google Sheets API
# ---------------------------------------------------------------------------
MYCASES_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
MYCASES_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Cases")
MYCASES_PASSWORD = os.environ.get("MYCASES_PASSWORD", "")
MYCASES_SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
MYCASES_TOKEN_HOURS = 12

MYCASES_FIELDS = [
    "id", "policeStation", "caseType", "crimeNo", "crimeYear",
    "sections", "complainant", "accused", "ioName", "priority",
    "court", "courtCaseNo", "stage", "nextHearing", "nextAction",
    "notes", "createdAt", "updatedAt"
]

api_app = Flask("clearexams_mycases_api")
CORS(
    api_app,
    origins=["https://clearexams.ink", "https://www.clearexams.ink"],
    supports_credentials=False,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)

def _issue_token() -> str:
    if not MYCASES_SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET is not configured")
    payload = {
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=MYCASES_TOKEN_HOURS)).timestamp()),
        "nonce": uuid.uuid4().hex,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(
        MYCASES_SESSION_SECRET.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256
    ).digest())
    return f"{body}.{sig}"

def _valid_token(token: str) -> bool:
    if not token or not MYCASES_SESSION_SECRET or "." not in token:
        return False
    try:
        body, sig = token.split(".", 1)
        expected = _b64url(hmac.new(
            MYCASES_SESSION_SECRET.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256
        ).digest())
        if not hmac.compare_digest(sig, expected):
            return False
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        return int(payload.get("exp", 0)) > int(datetime.now(timezone.utc).timestamp())
    except Exception:
        return False

def _authorized(req) -> bool:
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return _valid_token(header[7:].strip())

def _require_auth():
    if _authorized(request):
        return None
    return jsonify({"error": "Authentication required"}), 401

def _sheet_service():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
    info = json.loads(raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def _row_to_case(row):
    padded = list(row) + [""] * (len(MYCASES_FIELDS) - len(row))
    return {key: padded[i] if i < len(padded) else "" for i, key in enumerate(MYCASES_FIELDS)}

def _case_to_row(item):
    return [str(item.get(key, "") or "") for key in MYCASES_FIELDS]

def _clean_case(data, existing=None):
    existing = existing or {}
    now = datetime.now(timezone.utc).isoformat()
    item = {}
    for key in MYCASES_FIELDS:
        if key in ("createdAt", "updatedAt"):
            continue
        value = data.get(key, "") if isinstance(data, dict) else ""
        item[key] = str(value).strip() if value is not None else ""
    item["id"] = item.get("id") or existing.get("id") or f"case_{uuid.uuid4().hex}"
    item["createdAt"] = existing.get("createdAt") or str(data.get("createdAt", "") or "") or now
    item["updatedAt"] = now
    return item

def _read_cases():
    if not MYCASES_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is not configured")
    service = _sheet_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=MYCASES_SHEET_ID,
        range=f"{MYCASES_SHEET_NAME}!A2:R"
    ).execute()
    rows = result.get("values", [])
    return [_row_to_case(row) for row in rows if any(str(v).strip() for v in row)]

def _find_case(case_id):
    cases = _read_cases()
    for idx, item in enumerate(cases):
        if item.get("id") == case_id:
            return cases, idx, idx + 2, item
    return cases, -1, None, None

@api_app.get("/health")
def mycases_health():
    return jsonify({
        "ok": True,
        "service": "cdr-pdf-bot+mycases-api",
        "sheetConfigured": bool(MYCASES_SHEET_ID),
        "googleCredentialConfigured": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")),
        "passwordConfigured": bool(MYCASES_PASSWORD),
    })

@api_app.post("/api/login")
def mycases_login():
    if not MYCASES_PASSWORD or not MYCASES_SESSION_SECRET:
        return jsonify({"error": "Authentication is not configured"}), 503
    supplied = str((request.get_json(silent=True) or {}).get("password", ""))
    if not hmac.compare_digest(supplied.encode("utf-8"), MYCASES_PASSWORD.encode("utf-8")):
        return jsonify({"error": "Invalid password"}), 401
    return jsonify({"ok": True, "token": _issue_token(), "expiresInHours": MYCASES_TOKEN_HOURS})

@api_app.get("/api/session")
def mycases_session():
    return jsonify({"authenticated": _authorized(request)})

@api_app.get("/api/cases")
def mycases_list():
    denied = _require_auth()
    if denied:
        return denied
    try:
        return jsonify({"cases": _read_cases()})
    except Exception as exc:
        logging.exception("My Cases list failed")
        return jsonify({"error": str(exc)}), 500

@api_app.post("/api/cases")
def mycases_create():
    denied = _require_auth()
    if denied:
        return denied
    try:
        item = _clean_case(request.get_json(silent=True) or {})
        service = _sheet_service()
        service.spreadsheets().values().append(
            spreadsheetId=MYCASES_SHEET_ID,
            range=f"{MYCASES_SHEET_NAME}!A:R",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [_case_to_row(item)]}
        ).execute()
        return jsonify({"case": item}), 201
    except Exception as exc:
        logging.exception("My Cases create failed")
        return jsonify({"error": str(exc)}), 500

@api_app.put("/api/cases/<case_id>")
def mycases_update(case_id):
    denied = _require_auth()
    if denied:
        return denied
    try:
        cases, idx, row_number, existing = _find_case(case_id)
        if idx < 0:
            return jsonify({"error": "Case not found"}), 404
        data = request.get_json(silent=True) or {}
        data["id"] = case_id
        item = _clean_case(data, existing)
        service = _sheet_service()
        service.spreadsheets().values().update(
            spreadsheetId=MYCASES_SHEET_ID,
            range=f"{MYCASES_SHEET_NAME}!A{row_number}:R{row_number}",
            valueInputOption="USER_ENTERED",
            body={"values": [_case_to_row(item)]}
        ).execute()
        return jsonify({"case": item})
    except Exception as exc:
        logging.exception("My Cases update failed")
        return jsonify({"error": str(exc)}), 500

@api_app.delete("/api/cases/<case_id>")
def mycases_delete(case_id):
    denied = _require_auth()
    if denied:
        return denied
    try:
        cases, idx, row_number, existing = _find_case(case_id)
        if idx < 0:
            return jsonify({"error": "Case not found"}), 404
        service = _sheet_service()
        metadata = service.spreadsheets().get(spreadsheetId=MYCASES_SHEET_ID).execute()
        target = next(
            (s for s in metadata.get("sheets", []) if s.get("properties", {}).get("title") == MYCASES_SHEET_NAME),
            None
        )
        if not target:
            return jsonify({"error": "Cases sheet not found"}), 500
        sheet_id = target["properties"]["sheetId"]
        service.spreadsheets().batchUpdate(
            spreadsheetId=MYCASES_SHEET_ID,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number
                    }
                }
            }]}
        ).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        logging.exception("My Cases delete failed")
        return jsonify({"error": str(exc)}), 500

def start_mycases_api():
    port = int(os.environ.get("PORT", "8080"))
    api_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send a mobile number or IMEI to start again.")
    return ConversationHandler.END

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    api_thread = threading.Thread(target=start_mycases_api, daemon=True)
    api_thread.start()

    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("add", add_after_pdf), CommandHandler("change", change_number), CommandHandler("remove", remove_number), MessageHandler(filters.TEXT & ~filters.COMMAND, begin)],
        states={
            STATION_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, station_step)],
            CRIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, crime)],
            SECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, section)],
            FROM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, from_date)],
            TO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, to_date)],
            RELATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, relation)],
            ADD_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_more)],
            CHANGE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_number_step)],
            REMOVE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_number_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
