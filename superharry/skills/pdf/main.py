import json
import os
import sys
import textwrap
from pathlib import Path


def emit(ok, message):
    text = str(message)
    while len(json.dumps({"ok": ok, "message": text}, ensure_ascii=False).encode("utf-8")) > 14000:
        text = text[: max(1, len(text) * 3 // 4)] + "\n[truncated]"
    print(json.dumps({"ok": ok, "message": text}, ensure_ascii=False))


def output_path(name):
    if not isinstance(name, str) or not name or len(name) > 120 or Path(name).name != name or any(c in name for c in "\\/\x00\r\n") or not name.lower().endswith(".pdf"):
        raise ValueError("Choose a simple .pdf filename.")
    return Path(os.environ["SKILL_OUTPUT_DIR"]) / name


def main():
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise ValueError("Expected a JSON object.")
    operation = request.get("operation")
    if operation == "read":
        files = json.loads(os.environ.get("SKILL_INPUT_MANIFEST", "[]"))
        if len(files) != 1 or not files[0]["name"].lower().endswith(".pdf"):
            raise ValueError("Select exactly one PDF file.")
        reader = PdfReader(files[0]["path"])
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported.")
        parts = [(page.extract_text() or "") for page in reader.pages[:30]]
        text = "\n".join(parts)
        if not text.strip():
            return "No extractable text was found. Scanned PDFs require OCR, which this skill does not provide."
        return f"Extracted text from up to 30 pages (document has {len(reader.pages)} pages):\n{text}"
    if operation == "create":
        text = request.get("text")
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError("Provide text of at most 20,000 characters.")
        try:
            text.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError("PDF creation currently supports Latin-1 text only.") from None
        target = output_path(request.get("filename", "document.pdf"))
        document = canvas.Canvas(str(target), pagesize=(612, 792))
        y = 750
        for paragraph in text.splitlines() or [""]:
            for line in textwrap.wrap(paragraph, width=85, replace_whitespace=False) or [""]:
                if y < 50:
                    document.showPage()
                    y = 750
                document.drawString(45, y, line)
                y -= 15
        document.save()
        return f"Created {target.name}."
    raise ValueError("Unsupported PDF operation. Use read or create.")


try:
    emit(True, main())
except (ValueError, KeyError, TypeError, OSError, ImportError, json.JSONDecodeError) as error:
    emit(False, str(error) if isinstance(error, ValueError) else "Could not process the PDF with the available tools.")
except Exception:
    emit(False, "Could not process the PDF with the available tools.")
