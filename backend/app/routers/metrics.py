from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Conversation, Lead, Message, MetricsDaily, Tool, ToolCallLog
from app.schemas import MetricsOverview, MetricsPoint, StageCount, ToolStats

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


@router.get("/tools", response_model=list[ToolStats])
async def tools_stats(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uso por ferramenta (link de parcela, planos, transferência etc) —
    quantas vezes foi chamada, taxa de sucesso, e quantas conversas
    distintas usaram cada uma. Chat de teste não entra (ToolCallLog nunca
    grava linha pra ele)."""
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(
            ToolCallLog.tool_key,
            func.max(ToolCallLog.tool_name).label("tool_name"),
            func.count().label("total"),
            func.sum(case((ToolCallLog.success.is_(True), 1), else_=0)).label("success"),
            func.count(func.distinct(ToolCallLog.conversation_id)).label("distinct_conv"),
        )
        .where(ToolCallLog.company_id == company_id)
        .group_by(ToolCallLog.tool_key)
        .order_by(func.count().desc())
    )
    rows = result.all()
    stats = []
    for tool_key, tool_name, total, success, distinct_conv in rows:
        success = success or 0
        stats.append(
            ToolStats(
                tool_key=tool_key,
                tool_name=tool_name,
                total_calls=total,
                success_calls=success,
                failed_calls=total - success,
                distinct_conversations=distinct_conv,
                success_rate=(success / total) if total else None,
            )
        )
    return stats


@router.get("/featured-tools", response_model=list[ToolStats])
async def featured_tools_stats(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ferramentas marcadas como "destacar nas métricas" (tela Ferramentas)
    — vira card no topo do Painel. Ao contrário de /tools, sempre lista a
    ferramenta mesmo com zero chamadas ainda (é um card fixo, não uma
    tabela de "o que já teve uso")."""
    company_id = await resolve_company_id(current, db)
    tools_result = await db.execute(
        select(Tool).where(Tool.company_id == company_id, Tool.featured_in_metrics.is_(True))
    )
    featured_tools = tools_result.scalars().all()
    if not featured_tools:
        return []

    tool_ids = [t.id for t in featured_tools]
    result = await db.execute(
        select(
            ToolCallLog.tool_id,
            func.count().label("total"),
            func.sum(case((ToolCallLog.success.is_(True), 1), else_=0)).label("success"),
            func.count(func.distinct(ToolCallLog.conversation_id)).label("distinct_conv"),
        )
        .where(ToolCallLog.company_id == company_id, ToolCallLog.tool_id.in_(tool_ids))
        .group_by(ToolCallLog.tool_id)
    )
    stats_by_tool_id = {row.tool_id: row for row in result.all()}

    stats = []
    for tool in featured_tools:
        row = stats_by_tool_id.get(tool.id)
        total = row.total if row else 0
        success = (row.success if row else 0) or 0
        distinct_conv = row.distinct_conv if row else 0
        stats.append(
            ToolStats(
                tool_key=tool.tool_key,
                tool_name=tool.name,
                total_calls=total,
                success_calls=success,
                failed_calls=total - success,
                distinct_conversations=distinct_conv,
                success_rate=(success / total) if total else None,
            )
        )
    return stats
