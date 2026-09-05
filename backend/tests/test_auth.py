import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.routers import auth

    auth._failed_attempts.clear()
    yield
    auth._failed_attempts.clear()


@pytest.mark.asyncio
async def test_login_success(client, user):
    resp = await client.post(
        "/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


@pytest.mark.asyncio
async def test_login_wrong_password(client, user):
    resp = await client.post(
        "/auth/login", json={"email": "teste@movfit.com", "password": "senha-errada"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client):
    resp = await client.post(
        "/auth/login", json={"email": "nao-existe@movfit.com", "password": "qualquer"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_rate_limit_after_five_failures(client, user):
    for _ in range(5):
        resp = await client.post(
            "/auth/login", json={"email": "teste@movfit.com", "password": "senha-errada"}
        )
        assert resp.status_code == 401

    # 6ª tentativa: bloqueado mesmo sem ter passado da 5ª errada ainda ser
    # processada — e mesmo com a senha CERTA, o bloqueio já vale.
    resp = await client.post(
        "/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"}
    )
    assert resp.status_code == 429
