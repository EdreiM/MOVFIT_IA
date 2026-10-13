"""Gestão de usuários pelo painel — incluindo a conta restrita "vendas"."""
import pytest
import pytest_asyncio

from app.models import Company, User, UserCompany
from app.security import hash_password


@pytest_asyncio.fixture(autouse=True)
def _reset_rate_limit():
    from app.routers import auth

    auth._failed_attempts.clear()
    yield
    auth._failed_attempts.clear()


async def _login(client, email, password):
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp


async def _headers(client, email="teste@movfit.com", password="senha-teste-123"):
    resp = await _login(client, email, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def other_company_user(db_session):
    other = Company(name="Outra Empresa", slug="outra-empresa", plan="starter", status="active")
    db_session.add(other)
    await db_session.flush()
    u = User(
        email="alheio@outra.com", hashed_password=hash_password("senha-alheia-1"),
        full_name="Alheio", role="vendas", status="active",
    )
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserCompany(user_id=u.id, company_id=other.id))
    await db_session.commit()
    return u


@pytest.mark.asyncio
async def test_admin_creates_a_sales_only_account_that_works(client, user):
    headers = await _headers(client)
    created = await client.post(
        "/users",
        headers=headers,
        json={"email": "supervisora@movfit.com", "password": "senha-forte-1", "full_name": "Supervisora", "role": "vendas"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "vendas"

    sales_headers = await _headers(client, "supervisora@movfit.com", "senha-forte-1")
    assert (await client.get("/sales/ranking", headers=sales_headers)).status_code == 200
    # nada fora do ranking: nem a lista de usuários, nem criar outra conta
    assert (await client.get("/users", headers=sales_headers)).status_code == 403
    assert (
        await client.post(
            "/users",
            headers=sales_headers,
            json={"email": "x@y.com", "password": "senha-forte-1", "full_name": "X", "role": "admin"},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_create_user_validates_password_role_and_duplicates(client, user):
    headers = await _headers(client)
    base = {"email": "nova@movfit.com", "password": "senha-forte-1", "full_name": "Nova", "role": "vendas"}

    assert (await client.post("/users", headers=headers, json={**base, "password": "curta"})).status_code == 400
    assert (await client.post("/users", headers=headers, json={**base, "role": "dono-do-mundo"})).status_code == 400
    assert (await client.post("/users", headers=headers, json={**base, "role": "super_admin"})).status_code == 403
    assert (await client.post("/users", headers=headers, json=base)).status_code == 200
    assert (await client.post("/users", headers=headers, json=base)).status_code == 400  # e-mail repetido


@pytest.mark.asyncio
async def test_admin_cannot_promote_anyone_to_super_admin(client, user, db_session):
    headers = await _headers(client)
    me = await client.get("/auth/me", headers=headers)
    mine = me.json()["id"]

    escalate = await client.patch(f"/users/{mine}", headers=headers, json={"role": "super_admin"})
    assert escalate.status_code in (400, 403)
    assert (await client.get("/auth/me", headers=headers)).json()["role"] == "admin"

    other = await client.post(
        "/users",
        headers=headers,
        json={"email": "colega@movfit.com", "password": "senha-forte-1", "full_name": "Colega", "role": "vendas"},
    )
    promote = await client.patch(f"/users/{other.json()['id']}", headers=headers, json={"role": "super_admin"})
    assert promote.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_touch_users_of_another_company(client, user, other_company_user):
    headers = await _headers(client)
    target = f"/users/{other_company_user.id}"

    assert (await client.patch(target, headers=headers, json={"status": "inactive"})).status_code == 404
    assert (await client.patch(target, headers=headers, json={"password": "nova-senha-123"})).status_code == 404
    assert (await client.delete(target, headers=headers)).status_code == 404
    # e a lista só mostra a própria empresa
    emails = [u["email"] for u in (await client.get("/users", headers=headers)).json()]
    assert "alheio@outra.com" not in emails


@pytest.mark.asyncio
async def test_deactivating_blocks_login_and_existing_sessions(client, user):
    admin = await _headers(client)
    created = await client.post(
        "/users",
        headers=admin,
        json={"email": "temp@movfit.com", "password": "senha-forte-1", "full_name": "Temp", "role": "vendas"},
    )
    uid = created.json()["id"]
    temp = await _headers(client, "temp@movfit.com", "senha-forte-1")
    assert (await client.get("/sales/ranking", headers=temp)).status_code == 200

    assert (await client.patch(f"/users/{uid}", headers=admin, json={"status": "inactive"})).status_code == 200

    assert (await client.get("/sales/ranking", headers=temp)).status_code == 401  # sessão já aberta cai
    assert (await _login(client, "temp@movfit.com", "senha-forte-1")).status_code == 403

    assert (await client.patch(f"/users/{uid}", headers=admin, json={"status": "active"})).status_code == 200
    assert (await _login(client, "temp@movfit.com", "senha-forte-1")).status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_or_delete_self_and_password_reset_works(client, user):
    admin = await _headers(client)
    mine = (await client.get("/auth/me", headers=admin)).json()["id"]
    assert (await client.patch(f"/users/{mine}", headers=admin, json={"status": "inactive"})).status_code == 400
    assert (await client.delete(f"/users/{mine}", headers=admin)).status_code == 400

    created = await client.post(
        "/users",
        headers=admin,
        json={"email": "esqueceu@movfit.com", "password": "senha-forte-1", "full_name": "Esqueceu", "role": "vendas"},
    )
    uid = created.json()["id"]
    assert (await client.patch(f"/users/{uid}", headers=admin, json={"password": "curta"})).status_code == 400
    assert (await client.patch(f"/users/{uid}", headers=admin, json={"password": "senha-nova-99"})).status_code == 200
    assert (await _login(client, "esqueceu@movfit.com", "senha-forte-1")).status_code == 401
    assert (await _login(client, "esqueceu@movfit.com", "senha-nova-99")).status_code == 200
