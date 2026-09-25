import logging
from datetime import datetime, timezone

import bot
from flask import jsonify

DELETED_CASES_SHEET_NAME = "Deleted Cases"
DELETED_CASES_HEADERS = [
    "Case ID", "Police Station / Unit", "Case Type", "Crime / CSR / UDR No.", "Year",
    "Date of Occurrence", "Date of Registration", "Scene of Crime", "Sections / Offences",
    "Complainant", "Accused / Suspect", "Investigating Officer", "Priority", "Court",
    "Court Case No.", "Stage", "Next Hearing / Action Date", "Next Action", "Notes",
    "Created At", "Updated At", "Accused JSON", "Investigation JSON", "Tasks JSON",
    "Court Hearings JSON", "Timeline JSON", "Attachments JSON", "Deleted At", "Delete Reason"
]


def _deleted_range(a1):
    return f"'{DELETED_CASES_SHEET_NAME}'!{a1}"


def _ensure_deleted_cases_sheet():
    if not bot.MYCASES_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is not configured")
    service = bot._sheet_service()
    meta = service.spreadsheets().get(spreadsheetId=bot.MYCASES_SHEET_ID).execute()
    target = next(
        (s for s in meta.get("sheets", [])
         if s.get("properties", {}).get("title") == DELETED_CASES_SHEET_NAME),
        None,
    )
    if not target:
        service.spreadsheets().batchUpdate(
            spreadsheetId=bot.MYCASES_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": DELETED_CASES_SHEET_NAME}}}]},
        ).execute()
        meta = service.spreadsheets().get(spreadsheetId=bot.MYCASES_SHEET_ID).execute()
        target = next(
            (s for s in meta.get("sheets", [])
             if s.get("properties", {}).get("title") == DELETED_CASES_SHEET_NAME),
            None,
        )
    if not target:
        raise RuntimeError("Deleted Cases sheet could not be created")

    header = service.spreadsheets().values().get(
        spreadsheetId=bot.MYCASES_SHEET_ID,
        range=_deleted_range("A1:AC1"),
    ).execute().get("values", [])
    if not header or header[0] != DELETED_CASES_HEADERS:
        service.spreadsheets().values().update(
            spreadsheetId=bot.MYCASES_SHEET_ID,
            range=_deleted_range("A1:AC1"),
            valueInputOption="RAW",
            body={"values": [DELETED_CASES_HEADERS]},
        ).execute()
    return service


def _read_deleted_cases():
    service = _ensure_deleted_cases_sheet()
    result = service.spreadsheets().values().get(
        spreadsheetId=bot.MYCASES_SHEET_ID,
        range=_deleted_range("A2:AC"),
    ).execute()
    rows = result.get("values", [])
    items = []
    base_len = len(bot.MYCASES_FIELDS)
    for row in rows:
        if not any(str(v).strip() for v in row):
            continue
        padded = list(row) + [""] * max(0, len(DELETED_CASES_HEADERS) - len(row))
        item = bot._row_to_case(padded[:base_len])
        item["deletedAt"] = padded[base_len] if len(padded) > base_len else ""
        item["deleteReason"] = padded[base_len + 1] if len(padded) > base_len + 1 else ""
        items.append(item)
    return items


def _find_deleted_case(case_id):
    cases = _read_deleted_cases()
    for idx, item in enumerate(cases):
        if item.get("id") == case_id:
            return cases, idx, idx + 2, item
    return cases, -1, None, None


def _delete_sheet_row(sheet_name, row_number):
    service = bot._sheet_service()
    metadata = service.spreadsheets().get(spreadsheetId=bot.MYCASES_SHEET_ID).execute()
    target = next(
        (s for s in metadata.get("sheets", [])
         if s.get("properties", {}).get("title") == sheet_name),
        None,
    )
    if not target:
        raise RuntimeError(f"{sheet_name} sheet not found")
    service.spreadsheets().batchUpdate(
        spreadsheetId=bot.MYCASES_SHEET_ID,
        body={"requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": target["properties"]["sheetId"],
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }]},
    ).execute()


def safe_mycases_delete(case_id):
    denied = bot._require_auth()
    if denied:
        return denied
    try:
        cases, idx, row_number, existing = bot._find_case(case_id)
        if idx < 0 or not existing:
            return jsonify({"error": "Case not found"}), 404

        # Archive first. If this fails, the active case is never deleted.
        service = _ensure_deleted_cases_sheet()
        deleted_at = datetime.now(timezone.utc).isoformat()
        archive_row = bot._case_to_row(existing) + [deleted_at, "Deleted from My Cases"]
        service.spreadsheets().values().append(
            spreadsheetId=bot.MYCASES_SHEET_ID,
            range=_deleted_range("A:AC"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [archive_row]},
        ).execute()

        # Only after the archive append succeeds do we remove the active row.
        metadata = service.spreadsheets().get(spreadsheetId=bot.MYCASES_SHEET_ID).execute()
        target = next(
            (s for s in metadata.get("sheets", [])
             if s.get("properties", {}).get("title") == bot.MYCASES_SHEET_NAME),
            None,
        )
        if not target:
            raise RuntimeError("Cases sheet not found after archive")
        sheet_id = target["properties"]["sheetId"]
        service.spreadsheets().batchUpdate(
            spreadsheetId=bot.MYCASES_SHEET_ID,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number,
                    }
                }
            }]},
        ).execute()
        return jsonify({"ok": True, "archived": True, "deletedAt": deleted_at})
    except Exception as exc:
        logging.exception("Safe My Cases archive/delete failed")
        return jsonify({
            "error": "Case was not deleted because it could not be safely archived.",
            "detail": str(exc),
        }), 500


# Replace only the existing DELETE endpoint implementation; keep its URL/routing intact.
bot.api_app.view_functions["mycases_delete"] = safe_mycases_delete


@bot.api_app.get("/api/deleted-cases")
def mycases_deleted_list():
    denied = bot._require_auth()
    if denied:
        return denied
    try:
        return jsonify({"cases": _read_deleted_cases()})
    except Exception as exc:
        logging.exception("Deleted Cases list failed")
        return jsonify({"error": str(exc)}), 500


@bot.api_app.post("/api/deleted-cases/<case_id>/restore")
def mycases_deleted_restore(case_id):
    denied = bot._require_auth()
    if denied:
        return denied
    try:
        _, active_idx, _, _ = bot._find_case(case_id)
        if active_idx >= 0:
            return jsonify({"error": "Case is already active"}), 409

        _, idx, row_number, archived = _find_deleted_case(case_id)
        if idx < 0 or not archived:
            return jsonify({"error": "Deleted case not found"}), 404

        restored = bot._clean_case(archived, archived)
        service = bot._ensure_sheet()
        service.spreadsheets().values().append(
            spreadsheetId=bot.MYCASES_SHEET_ID,
            range=f"{bot.MYCASES_SHEET_NAME}!A:AA",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [bot._case_to_row(restored)]},
        ).execute()

        _delete_sheet_row(DELETED_CASES_SHEET_NAME, row_number)
        return jsonify({"ok": True, "case": restored})
    except Exception as exc:
        logging.exception("Deleted Cases restore failed")
        return jsonify({"error": str(exc)}), 500


@bot.api_app.delete("/api/deleted-cases/<case_id>")
def mycases_deleted_permanent_delete(case_id):
    denied = bot._require_auth()
    if denied:
        return denied
    try:
        _, idx, row_number, _ = _find_deleted_case(case_id)
        if idx < 0:
            return jsonify({"error": "Deleted case not found"}), 404
        _delete_sheet_row(DELETED_CASES_SHEET_NAME, row_number)
        return jsonify({"ok": True})
    except Exception as exc:
        logging.exception("Deleted Cases permanent delete failed")
        return jsonify({"error": str(exc)}), 500

if __name__ == "__main__":
    bot.main()
