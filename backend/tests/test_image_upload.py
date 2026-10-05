from io import BytesIO

from PIL import Image

from app.services.image_upload import MAX_STORED_BYTES, optimize_uploaded_image


def _make_png(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(220, 50, 120))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_optimize_uploaded_image_shrinks_large_png():
    raw = _make_png(2400, 4200)
    optimized, ext = optimize_uploaded_image(raw)

    assert ext == ".jpg"
    assert len(optimized) <= MAX_STORED_BYTES
    out = Image.open(BytesIO(optimized))
    assert max(out.size) <= 1600
