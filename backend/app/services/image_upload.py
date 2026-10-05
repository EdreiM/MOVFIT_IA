"""Otimização de imagens enviadas pelo painel (banner de promo, etc.)."""
from __future__ import annotations

import logging
import os
from io import BytesIO
from uuid import UUID

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_STORED_BYTES = 2 * 1024 * 1024
MAX_LONGEST_SIDE = 1600

PROMOTIONS_UPLOADS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "uploads", "promotions"
)
WHATSAPP_SAFE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def optimize_uploaded_image(content: bytes) -> tuple[bytes, str]:
    """Redimensiona e comprime artes grandes (ex.: banner Outubro Rosa em PNG).

    Aceita uploads pesados e grava JPEG leve pronto para WhatsApp e preview no painel.
    """
    img = ImageOps.exif_transpose(Image.open(BytesIO(content)))
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > MAX_LONGEST_SIDE:
        scale = MAX_LONGEST_SIDE / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    # JPEG primeiro — WhatsApp/Evolution não tratam WEBP como imagem inline
    # de forma confiável (vira documento .txt/.bin no cliente).
    for quality in (85, 75, 65, 55, 45):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_STORED_BYTES:
            return data, ".jpg"

    while max(img.size) > 800:
        w, h = img.size
        img = img.resize((int(w * 0.85), int(h * 0.85)), Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_STORED_BYTES:
            return data, ".jpg"

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return buf.getvalue(), ".jpg"


def _promotion_public_url(public_base_url: str, filename: str) -> str:
    return f"{public_base_url.rstrip('/')}/uploads/promotions/{filename}"


def ensure_whatsapp_delivery_url(
    promotion_id: UUID,
    image_url: str | None,
    public_base_url: str,
) -> str | None:
    """Garante URL PNG/JPG para o WTS/WhatsApp — WEBP vira documento .txt no cliente."""
    if not image_url:
        return None

    clean = image_url.split("?", 1)[0].rstrip("/")
    ext = os.path.splitext(clean)[1].lower()
    if ext in WHATSAPP_SAFE_IMAGE_EXTENSIONS:
        return image_url

    if ext != ".webp":
        return image_url

    src_name = os.path.basename(clean)
    src_path = os.path.join(PROMOTIONS_UPLOADS_DIR, src_name)
    if not os.path.isfile(src_path):
        alt_path = os.path.join(PROMOTIONS_UPLOADS_DIR, f"{promotion_id}.webp")
        if os.path.isfile(alt_path):
            src_path = alt_path
        else:
            logger.warning("Banner WEBP da promoção %s não encontrado em disco: %s", promotion_id, src_name)
            return image_url

    dst_name = f"{promotion_id}.jpg"
    dst_path = os.path.join(PROMOTIONS_UPLOADS_DIR, dst_name)
    try:
        img = ImageOps.exif_transpose(Image.open(src_path))
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        os.makedirs(PROMOTIONS_UPLOADS_DIR, exist_ok=True)
        img.save(dst_path, format="JPEG", quality=85, optimize=True)
        if src_path != dst_path and os.path.isfile(src_path):
            os.remove(src_path)
    except Exception:
        logger.exception("Falha ao converter banner WEBP da promoção %s para JPG", promotion_id)
        return image_url

    return _promotion_public_url(public_base_url, dst_name)
