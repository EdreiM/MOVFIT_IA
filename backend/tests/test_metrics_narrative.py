"""Relatório narrativo das métricas."""
from datetime import datetime, timezone

from app.schemas import MetricsOverview, StageCount, ToolStats
from app.services.metrics_narrative import NarrativeContext, build_metrics_narrative


def _overview(**kwargs) -> MetricsOverview:
    defaults = dict(
        conversations_total=0,
        messages_inbound=0,
        messages_outbound=0,
        ai_resolved=0,
        human_resolved=0,
        avg_response_seconds=None,
        ai_resolution_rate=None,
        students_total=0,
        transferred_total=0,
        with_human_total=0,
        cancellation_requests_total=0,
        physical_evals_scheduled_total=0,
    )
    defaults.update(kwargs)
    return MetricsOverview(**defaults)


def test_narrative_empty_state():
    ctx = NarrativeContext(
        ai_name="Mônica",
        overview=_overview(),
        open_conversations=0,
        with_human_conversations=0,
        leads_total=0,
        messages_inbound_last_7_days=0,
        messages_outbound_last_7_days=0,
        funnel=[],
        tool_stats=[],
        generated_at=datetime.now(timezone.utc),
    )
    paragraphs = build_metrics_narrative(ctx)
    assert len(paragraphs) == 2
    assert "Mônica" in paragraphs[0]
    assert "Ainda não recebi" in paragraphs[1]


def test_narrative_with_real_activity():
    ctx = NarrativeContext(
        ai_name="Mônica",
        overview=_overview(
            conversations_total=42,
            messages_inbound=180,
            messages_outbound=210,
            ai_resolved=20,
            human_resolved=5,
            ai_resolution_rate=0.8,
            students_total=12,
            transferred_total=8,
            cancellation_requests_total=2,
            physical_evals_scheduled_total=6,
        ),
        open_conversations=3,
        with_human_conversations=1,
        leads_total=55,
        messages_inbound_last_7_days=40,
        messages_outbound_last_7_days=52,
        funnel=[
            StageCount(stage="interessado", count=10),
            StageCount(stage="qualificado", count=5),
        ],
        tool_stats=[
            ToolStats(
                tool_key="enviar_imagens_planos",
                tool_name="Enviar planos",
                total_calls=15,
                success_calls=15,
                failed_calls=0,
                distinct_conversations=15,
                success_rate=1.0,
            ),
            ToolStats(
                tool_key="consultar_agendamento_horarios",
                tool_name="Horários",
                total_calls=20,
                success_calls=18,
                failed_calls=2,
                distinct_conversations=12,
                success_rate=0.9,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )
    paragraphs = build_metrics_narrative(ctx)
    text = " ".join(paragraphs)
    assert "42 conversas" in text
    assert "80%" in text
    assert "12 alunos" in text
    assert "6 avaliações físicas" in text
    assert "material de planos" in text
    assert "cancelamento" in text
    assert "interessados" in text
    assert paragraphs[0].startswith("Oi!")
