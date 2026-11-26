"""Verify repository document skills inside the real isolated Docker worker."""
import base64
import json
import io
import os
import zipfile
from pathlib import Path

from scripts.skill_sandbox import run_skill


image = os.environ.get("SKILL_TEST_IMAGE", "superharry-docs-test:local")
root = Path(__file__).resolve().parent.parent / "superharry" / "skills"


def invoke(name, request, files=()):
    source = (root / name / "main.py").read_text(encoding="utf-8")
    payload = {"operation": "run", "files": {"main.py": source}, "entrypoint": "main.py", "input": json.dumps(request),
               "dependencies": [], "wheels": {}, "output_protocol": "structured"}
    if files:
        payload["input_files"] = [{"id": index + 1, "name": filename, "data": base64.b64encode(content).decode()} for index, (filename, content) in enumerate(files)]
        payload["requires_file_inputs"] = True
    return run_skill(payload, timeout=30, image=image)


def main():
    cases = [
        ("pdf", {"operation": "create", "filename": "report.pdf", "text": "Synthetic información, año"}, "report.pdf"),
        ("docx", {"operation": "create", "filename": "report.docx", "title": "Synthetic title", "text": "Synthetic document content"}, "report.docx"),
        ("xlsx", {"operation": "create", "filename": "report.xlsx", "sheets": [{"name": "Data", "rows": [["Name", "Count"], ["Synthetic", 3]]}]}, "report.xlsx"),
    ]
    for name, request, filename in cases:
        created = invoke(name, request)
        assert created["status"] == "complete", (name, created)
        assert [item["name"] for item in created["artifacts"]] == [filename], created
        content = base64.b64decode(created["artifacts"][0]["data"])
        read = invoke(name, {"operation": "read"}, [(filename, content)])
        assert read["status"] == "complete" and "Synthetic" in read["output"], (name, read)
        if name == "pdf":
            assert "información, año" in read["output"], read
        assert not read.get("artifacts"), read
        unsupported = invoke(name, {"operation": "unsupported"})
        assert unsupported["status"] == "failed" and "Unsupported" in unsupported.get("message", ""), unsupported
        invalid = invoke(name, {"operation": "create", "filename": "../escape." + name, "text": "bad", "sheets": [{"name": "A", "rows": []}]})
        assert invalid["status"] == "failed" and not invalid.get("artifacts"), invalid
    oversized_archive = io.BytesIO()
    with zipfile.ZipFile(oversized_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.xml", b"0" * (33 * 1024 * 1024))
    for name in ("docx", "xlsx"):
        result = invoke(name, {"operation": "read"}, [(f"large.{name}", oversized_archive.getvalue())])
        assert result["status"] == "failed" and "expands beyond" in result.get("message", ""), result
    print("Document skill Docker smoke passed")


if __name__ == "__main__":
    main()
