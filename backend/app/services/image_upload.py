"""Otimização de imagens enviadas pelo painel (banner de promo, etc.)."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_STORED_BYTES = 2 * 1024 * 1024
MAX_LONGEST_SIDE = 1600


def optimize_uploaded_image(content: bytes) -> tuple[bytes, str]:
    """Redimensiona e comprime artes grandes (ex.: banner Outubro Rosa em PNG).

    Aceita uploads pesados e grava uma versão leve (.webp ou .jpg) pronta
    para WhatsApp e preview no painel.
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

    for quality in (85, 75, 65, 55, 45):
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=quality, method=6)
        data = buf.getvalue()
        if len(data) <= MAX_STORED_BYTES:
            return data, ".webp"

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
