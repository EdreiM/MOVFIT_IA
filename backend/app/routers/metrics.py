from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Conversation, Lead, Message, MetricsDaily
from app.schemas import MetricsOverview, MetricsPoint, StageCount

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/overview", response_model=MetricsOverview)
async def metrics_overview(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)

    conv_total = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.company_id == company_id, Conversation.channel != "test_console")
    )
    inbound = await db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.company_id == company_id,
            Message.direction == "inbound",
            Conversation.channel != "test_console",
        )
    )
    outbound = await db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.company_id == company_id,
            Message.direction == "outbound",
            Conversation.channel != "test_console",
        )
    )
    ai_resolved = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.company_id == company_id,
            Conversation.status == "resolved",
            Conversation.ai_enabled.is_(True),
            Conversation.channel != "test_console",
        )
    )
    human_resolved = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.company_id == company_id,
            Conversation.status == "resolved",
            Conversation.ai_enabled.is_(False),
            Conversation.channel != "test_console",
        )
    )

    result = await db.execute(
        select(func.avg(MetricsDaily.avg_response_seconds)).where(MetricsDaily.company_id == company_id)
    )
    avg_resp = result.scalar()

    ai_r = ai_resolved or 0
    hu_r = human_resolved or 0
    rate = None
    if ai_r + hu_r > 0:
        rate = ai_r / (ai_r + hu_r)

    return MetricsOverview(
        conversations_total=conv_total or 0,
        messages_inbound=inbound or 0,
        messages_outbound=outbound or 0,
        ai_resolved=ai_r,
        human_resolved=hu_r,
        avg_response_seconds=float(avg_resp) if avg_resp is not None else None,
        ai_resolution_rate=rate,
    )


@router.get("/timeseries", response_model=list[MetricsPoint])
async def metrics_timeseries(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(MetricsDaily)
        .where(MetricsDaily.company_id == company_id)
        .order_by(MetricsDaily.day.asc())
        .limit(30)
    )
    rows = result.scalars().all()
    return [
        MetricsPoint(
            day=r.day.isoformat(),
            conversations_total=r.conversations_total,
            messages_inbound=r.messages_inbound,
            messages_outbound=r.messages_outbound,
        )
        for r in rows
    ]


@router.get("/leads-funnel", response_model=list[StageCount])
async def leads_funnel(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(Lead.stage, func.count())
        .where(Lead.company_id == company_id)
        .group_by(Lead.stage)
    )
    return [StageCount(stage=stage, count=count) for stage, count in result.all()]
