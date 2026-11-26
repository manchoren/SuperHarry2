"""Verify image operations inside the real isolated Docker worker."""
import base64
import io
import json

from PIL import Image, PngImagePlugin

from tests.document_skills_smoke import invoke


def output_image(result):
    assert result["status"] == "complete", result
    data = base64.b64decode(result["artifacts"][0]["data"])
    return Image.open(io.BytesIO(data)), data


def main():
    source = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "synthetic private metadata")
    image = Image.new("RGBA", (40, 30), (0, 0, 255, 0))
    for x in range(10, 30):
        for y in range(10, 20):
            image.putpixel((x, y), (255, 0, 0, 255))
    image.save(source, "PNG", pnginfo=metadata)
    original = source.getvalue()
    files = [("source.png", original)]

    info = invoke("image", {"operation": "info"}, files)
    dimensions = json.loads(info["output"])
    assert info["status"] == "complete" and dimensions["width"] == 40 and dimensions["height"] == 30, info
    assert "not visually interpreted" in dimensions["note"] and not info.get("artifacts"), info
    resized, payload = output_image(invoke("image", {"operation": "resize", "width": 20, "height": 15, "filename": "small.png"}, files))
    assert resized.size == (20, 15) and resized.format == "PNG" and b"synthetic private metadata" not in payload
    cropped, _ = output_image(invoke("image", {"operation": "crop", "box": [10, 5, 30, 25], "filename": "crop.png"}, files))
    assert cropped.size == (20, 20)
    converted, _ = output_image(invoke("image", {"operation": "convert", "format": "jpeg", "filename": "converted.jpg"}, files))
    assert converted.format == "JPEG" and converted.mode == "RGB" and min(converted.getpixel((0, 0))) >= 240
    jpeg = io.BytesIO()
    converted.save(jpeg, "JPEG")
    png, _ = output_image(invoke("image", {"operation": "convert", "format": "png", "filename": "converted.png"}, [("converted.jpg", jpeg.getvalue())]))
    assert png.format == "PNG" and png.size == (40, 30)
    rotated = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (40, 30), "blue").save(rotated, "JPEG", exif=exif)
    info = invoke("image", {"operation": "info"}, [("rotated.jpg", rotated.getvalue())])
    assert (json.loads(info["output"])["width"], json.loads(info["output"])["height"]) == (30, 40), info

    errors = [
        ({"operation": "describe"}, files),
        ({"operation": "resize", "width": 0, "height": 10, "filename": "bad.png"}, files),
        ({"operation": "crop", "box": [0, 0, 100, 100], "filename": "bad.png"}, files),
        ({"operation": "convert", "format": "jpeg", "filename": "bad.png"}, files),
        ({"operation": "convert", "format": "jpeg", "filename": "../bad.jpg"}, files),
        ({"operation": "info"}, [("wrong.jpg", original)]),
    ]
    oversized = io.BytesIO()
    Image.new("RGB", (2001, 2000), "white").save(oversized, "PNG")
    errors.append(({"operation": "info"}, [("large.png", oversized.getvalue())]))
    animated = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(animated, "PNG", save_all=True, append_images=[Image.new("RGB", (8, 8), "black")], duration=100)
    errors.append(({"operation": "info"}, [("animated.png", animated.getvalue())]))
    for request, inputs in errors:
        result = invoke("image", request, inputs)
        assert result["status"] == "failed" and result.get("message") and not result.get("artifacts"), result
    assert source.getvalue() == original
    print("Image skill Docker smoke passed")


if __name__ == "__main__":
    main()
