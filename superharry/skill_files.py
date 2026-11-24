from pathlib import PurePosixPath


MAX_SKILL_FILE_BYTES = 256 * 1024


def validate_skill_files(files):
    """Validate the untrusted file mapping before materializing skill files."""
    if not isinstance(files, dict):
        raise ValueError("Enter a JSON object of file names and text.")
    total_bytes = 0
    for name, content in files.items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("Files must use safe relative names and text content.")
        path = PurePosixPath(name)
        if (
            not name or "\\" in name or ":" in name or "\x00" in name or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
        ):
            raise ValueError("Files must use safe relative names and text content.")
        try:
            total_bytes += len(name.encode("utf-8")) + len(content.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Files must contain valid UTF-8 text.") from None
    if total_bytes > MAX_SKILL_FILE_BYTES:
        raise ValueError("Skill files exceed the 256 KiB limit.")
    return files
