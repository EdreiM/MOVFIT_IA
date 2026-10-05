import uuid
from io import BytesIO

from PIL import Image

from app.services import image_upload as image_upload_module
from app.services.image_upload import (
    MAX_STORED_BYTES,
    ensure_whatsapp_delivery_url,
    optimize_uploaded_image,
)


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


def test_ensure_whatsapp_delivery_url_keeps_png():
    promo_id = uuid.uuid4()
    url = f"https://movfit-ia.xmov.com.br/api/uploads/promotions/{promo_id}.png"
    assert ensure_whatsapp_delivery_url(promo_id, url, "https://movfit-ia.xmov.com.br/api") == url


def test_ensure_whatsapp_delivery_url_converts_webp(tmp_path, monkeypatch):
    promo_id = uuid.uuid4()
    monkeypatch.setattr(image_upload_module, "PROMOTIONS_UPLOADS_DIR", str(tmp_path))

    webp_path = tmp_path / f"{promo_id}.webp"
    Image.new("RGB", (120, 80), color=(220, 50, 120)).save(webp_path, format="WEBP")

    source_url = f"https://movfit-ia.xmov.com.br/api/uploads/promotions/{promo_id}.webp"
    delivery_url = ensure_whatsapp_delivery_url(
        promo_id,
        source_url,
        "https://movfit-ia.xmov.com.br/api",
    )

    assert delivery_url == f"https://movfit-ia.xmov.com.br/api/uploads/promotions/{promo_id}.jpg"
    assert (tmp_path / f"{promo_id}.jpg").is_file()
    assert not webp_path.is_file()
