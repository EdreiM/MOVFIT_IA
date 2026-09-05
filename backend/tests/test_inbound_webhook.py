import secrets

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def integration(db_session, company):
    from app.models import Integration

    integ = Integration(
        company_id=company.id,
        name="Integração Teste",
        integration_type="webhook",
        adapter_key="generic_mapping",
        inbound_secret=secrets.token_urlsafe(16),
        field_mapping={},
    )
    db_session.add(integ)
    await db_session.commit()
    await db_session.refresh(integ)
    return integ


@pytest.mark.asyncio
async def test_inbound_webhook_creates_conversation_with_integration(client, integration):
    resp = await client.post(
        f"/webhooks/inbound/{integration.id}/{integration.inbound_secret}",
        json={"text": "Olá, quero saber dos planos", "from": "5511999998888"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"

    from app.database import AsyncSessionLocal
    from app.models import Conversation, Message

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.contact_phone == "5511999998888")
        )
        conv = result.scalar_one()
        assert conv.integration_id == integration.id

        result = await session.execute(
            select(Message).where(Message.conversation_id == conv.id)
        )
        msgs = result.scalars().all()
        assert len(msgs) == 1
        assert msgs[0].text == "Olá, quero saber dos planos"
        assert msgs[0].actor == "customer"


@pytest.mark.asyncio
async def test_inbound_webhook_wrong_secret_is_rejected(client, integration):
    resp = await client.post(
        f"/webhooks/inbound/{integration.id}/segredo-errado",
        json={"text": "oi", "from": "5511999998888"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inbound_webhook_unknown_integration_is_rejected(client):
    import uuid

    resp = await client.post(
        f"/webhooks/inbound/{uuid.uuid4()}/qualquer-segredo",
        json={"text": "oi", "from": "5511999998888"},
    )
    assert resp.status_code == 404
