import json
import os
import sys
import zipfile
from pathlib import Path


def emit(ok, message):
    text = str(message)
    while len(json.dumps({"ok": ok, "message": text}, ensure_ascii=False).encode("utf-8")) > 14000:
        text = text[: max(1, len(text) * 3 // 4)] + "\n[truncated]"
    print(json.dumps({"ok": ok, "message": text}, ensure_ascii=False))


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > 32 * 1024 * 1024:
            raise ValueError("The workbook expands beyond the supported limit.")


def output_path(name):
    if not isinstance(name, str) or not name or len(name) > 120 or Path(name).name != name or any(c in name for c in "\\/\x00\r\n") or not name.lower().endswith(".xlsx"):
        raise ValueError("Choose a simple .xlsx filename.")
    return Path(os.environ["SKILL_OUTPUT_DIR"]) / name


def main():
    from openpyxl import Workbook, load_workbook

    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise ValueError("Expected a JSON object.")
    if request.get("operation") == "read":
        files = json.loads(os.environ.get("SKILL_INPUT_MANIFEST", "[]"))
        if len(files) != 1 or not files[0]["name"].lower().endswith(".xlsx"):
            raise ValueError("Select exactly one XLSX file.")
        validate_archive(files[0]["path"])
        with open(files[0]["path"], "rb") as source:
            workbook = load_workbook(source, read_only=True, data_only=False)
            sheets = []
            for sheet in workbook.worksheets[:5]:
                rows = [list(row) for row in sheet.iter_rows(max_row=1000, max_col=min(sheet.max_column or 1, 30), values_only=True)]
                sheets.append({"name": sheet.title, "rows": rows})
            workbook.close()
        return json.dumps({"sheets": sheets, "limits": "First 5 sheets, 1000 rows and 30 columns; formulas are not recalculated."}, ensure_ascii=False, default=str)
    if request.get("operation") == "create":
        sheets = request.get("sheets")
        if not isinstance(sheets, list) or not 1 <= len(sheets) <= 5:
            raise ValueError("Provide between one and five sheets.")
        workbook = Workbook()
        workbook.remove(workbook.active)
        names = set()
        for sheet in sheets:
            if not isinstance(sheet, dict) or not isinstance(sheet.get("name"), str) or not isinstance(sheet.get("rows"), list) or len(sheet["rows"]) > 1000:
                raise ValueError("Provide sheet names and at most 1,000 rows per sheet.")
            if not 1 <= len(sheet["name"]) <= 31 or any(char in sheet["name"] for char in r'\/*?:[]') or sheet["name"].casefold() in names:
                raise ValueError("Sheet names must be unique, valid and at most 31 characters.")
            names.add(sheet["name"].casefold())
            worksheet = workbook.create_sheet(title=sheet["name"])
            for row in sheet["rows"]:
                if not isinstance(row, list) or len(row) > 30 or any(not isinstance(value, (str, int, float, bool, type(None))) for value in row):
                    raise ValueError("Rows may contain up to 30 scalar values.")
                worksheet.append(row)
        target = output_path(request.get("filename", "workbook.xlsx"))
        workbook.save(target)
        return f"Created {target.name}. Formulas, if present, are not recalculated."
    raise ValueError("Unsupported XLSX operation. Use read or create.")


try:
    emit(True, main())
except (ValueError, KeyError, TypeError, OSError, ImportError, zipfile.BadZipFile, json.JSONDecodeError) as error:
    emit(False, str(error) if isinstance(error, ValueError) else "Could not process the XLSX with the available tools.")
except Exception:
    emit(False, "Could not process the XLSX with the available tools.")
