import json
import os
import sys
from pathlib import Path


MAX_PIXELS = 4_000_000
MAX_SIDE = 4096


def emit(ok, message):
    text = str(message)
    while len(json.dumps({"ok": ok, "message": text}, ensure_ascii=False).encode("utf-8")) > 14000:
        text = text[: max(1, len(text) * 3 // 4)] + "\n[truncated]"
    print(json.dumps({"ok": ok, "message": text}, ensure_ascii=False))


def destination(name):
    if not isinstance(name, str) or not name or len(name) > 120 or Path(name).name != name or any(char in name for char in "\\/\x00\r\n"):
        raise ValueError("Choose a simple PNG or JPEG filename.")
    lower = name.lower()
    if lower.endswith(".png"):
        format_name = "PNG"
    elif lower.endswith((".jpg", ".jpeg")):
        format_name = "JPEG"
    else:
        raise ValueError("Choose a filename ending in .png, .jpg or .jpeg.")
    return Path(os.environ["SKILL_OUTPUT_DIR"]) / name, format_name


def main():
    from PIL import Image, ImageOps

    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise ValueError("Expected a JSON object.")
    operation = request.get("operation")
    if operation not in ("info", "resize", "crop", "convert"):
        raise ValueError("Unsupported image operation. Use info, resize, crop or convert.")
    files = json.loads(os.environ.get("SKILL_INPUT_MANIFEST", "[]"))
    if len(files) != 1 or not files[0]["name"].lower().endswith((".png", ".jpg", ".jpeg")):
        raise ValueError("Select exactly one PNG or JPEG image.")
    with Image.open(files[0]["path"]) as source:
        if source.format not in ("PNG", "JPEG") or source.width * source.height > MAX_PIXELS or max(source.size) > 8192:
            raise ValueError("Choose a PNG or JPEG image within the four-million-pixel limit.")
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("Animated images are not supported.")
        if (source.format == "PNG") != files[0]["name"].lower().endswith(".png"):
            raise ValueError("The image filename does not match its format.")
        original_format = source.format
        image = ImageOps.exif_transpose(source)
        if operation == "info":
            return json.dumps({"format": original_format, "width": image.width, "height": image.height, "mode": image.mode,
                               "note": "Image content was not visually interpreted."})
        if operation == "resize":
            width, height = request.get("width"), request.get("height")
            if type(width) is not int or type(height) is not int or not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE) or width * height > MAX_PIXELS:
                raise ValueError("Resize dimensions must be positive, at most 4096 per side and at most four million pixels.")
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        elif operation == "crop":
            box = request.get("box")
            if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
                raise ValueError("Crop requires four integer coordinates: left, top, right, bottom.")
            left, top, right, bottom = box
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError("Crop coordinates must define a rectangle within the image.")
            image = image.crop((left, top, right, bottom))
        target, format_name = destination(request.get("filename", "image.png"))
        if operation == "convert":
            desired = request.get("format", "").lower() if isinstance(request.get("format"), str) else ""
            if desired not in ("png", "jpeg", "jpg") or (format_name == "PNG") != (desired == "png"):
                raise ValueError("The conversion format must match the PNG or JPEG filename.")
        if format_name == "JPEG":
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "white")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            image = flattened
        image.save(target, format=format_name)
        return f"Created {target.name} ({image.width} × {image.height}). Metadata was not retained" + ("; transparency was flattened onto white." if format_name == "JPEG" else ".")


try:
    emit(True, main())
except (ValueError, KeyError, TypeError, OSError, ImportError, json.JSONDecodeError) as error:
    emit(False, str(error) if isinstance(error, ValueError) else "Could not process the image with the available tools.")
except Exception:
    emit(False, "Could not process the image with the available tools.")
