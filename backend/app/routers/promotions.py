import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Promotion
from app.schemas import PromotionCreate, PromotionOut, PromotionUpdate

router = APIRouter(prefix="/promotions", tags=["promotions"])

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "promotions")
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


async def _get_promotion_or_404(db: AsyncSession, promotion_id: UUID, company_id: UUID) -> Promotion:
    promotion = await db.get(Promotion, promotion_id)
    if not promotion or promotion.company_id != company_id:
        raise HTTPException(status_code=404, detail="Promoção não encontrada")
    return promotion


@router.get("", response_model=list[PromotionOut])
async def list_promotions(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(Promotion)
        .where(Promotion.company_id == company_id)
        .order_by(Promotion.sort_order.asc(), Promotion.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=PromotionOut)
async def create_promotion(
    payload: PromotionCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    data = payload.model_dump()
    data["unit_ids"] = [str(uid) for uid in data.get("unit_ids") or []]
    promotion = Promotion(company_id=company_id, **data)
    db.add(promotion)
    await db.flush()
    await db.refresh(promotion)
    return promotion


@router.patch("/{promotion_id}", response_model=PromotionOut)
async def update_promotion(
    promotion_id: UUID,
    payload: PromotionUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    promotion = await _get_promotion_or_404(db, promotion_id, company_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "unit_ids" and value is not None:
            setattr(promotion, key, [str(uid) for uid in value])
        else:
            setattr(promotion, key, value)
    await db.flush()
    await db.refresh(promotion)
    return promotion


@router.post("/{promotion_id}/image", response_model=PromotionOut)
async def upload_promotion_image(
    promotion_id: UUID,
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    promotion = await _get_promotion_or_404(db, promotion_id, company_id)

    ext = ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Envie uma imagem JPEG, PNG ou WEBP")

    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Imagem muito grande (máximo 5MB)")

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    filename = f"{promotion.id}{ext}"
    with open(os.path.join(UPLOADS_DIR, filename), "wb") as f:
        f.write(content)

    settings = get_settings()
    promotion.image_url = f"{settings.public_base_url.rstrip('/')}/uploads/promotions/{filename}"
    await db.flush()
    await db.refresh(promotion)
    return promotion


@router.delete("/{promotion_id}", status_code=204)
async def delete_promotion(
    promotion_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    promotion = await _get_promotion_or_404(db, promotion_id, company_id)
    await db.delete(promotion)
