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

if __name__ == "__main__":
    bot.main()
