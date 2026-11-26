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
            raise ValueError("The document expands beyond the supported limit.")


def output_path(name):
    if not isinstance(name, str) or not name or len(name) > 120 or Path(name).name != name or any(c in name for c in "\\/\x00\r\n") or not name.lower().endswith(".docx"):
        raise ValueError("Choose a simple .docx filename.")
    return Path(os.environ["SKILL_OUTPUT_DIR"]) / name


def main():
    from docx import Document

    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise ValueError("Expected a JSON object.")
    if request.get("operation") == "read":
        files = json.loads(os.environ.get("SKILL_INPUT_MANIFEST", "[]"))
        if len(files) != 1 or not files[0]["name"].lower().endswith(".docx"):
            raise ValueError("Select exactly one DOCX file.")
        validate_archive(files[0]["path"])
        document = Document(files[0]["path"])
        paragraphs = [paragraph.text for paragraph in document.paragraphs[:200]]
        tables = [[[cell.text for cell in row.cells[:30]] for row in table.rows[:100]] for table in document.tables[:20]]
        return json.dumps({"paragraphs": paragraphs, "tables": tables, "limits": "First 200 paragraphs and 20 tables."}, ensure_ascii=False)
    if request.get("operation") == "create":
        text, title = request.get("text"), request.get("title", "")
        if not isinstance(text, str) or len(text) > 20000 or not isinstance(title, str) or len(title) > 200:
            raise ValueError("Provide a title up to 200 characters and text up to 20,000 characters.")
        target = output_path(request.get("filename", "document.docx"))
        document = Document()
        if title:
            document.add_heading(title, 0)
        for paragraph in text.splitlines() or [""]:
            document.add_paragraph(paragraph)
        document.save(target)
        return f"Created {target.name}."
    raise ValueError("Unsupported DOCX operation. Use read or create.")


try:
    emit(True, main())
except (ValueError, KeyError, TypeError, OSError, ImportError, zipfile.BadZipFile, json.JSONDecodeError) as error:
    emit(False, str(error) if isinstance(error, ValueError) else "Could not process the DOCX with the available tools.")
except Exception:
    emit(False, "Could not process the DOCX with the available tools.")
