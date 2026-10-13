"""Cria (ou atualiza a senha de) um usuário do painel.

Uso, no console do container do backend:

    python -m app.create_user --email maria@empresa.com --name "Maria Silva" \
        --role vendas --company movfit

Sem --password, a senha é pedida no terminal (não aparece na tela nem fica no
histórico). Papéis: admin | vendas (só o ranking de vendas).
"""
import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Company, User, UserCompany
from app.security import hash_password

ROLES = ("admin", "vendas")
MIN_PASSWORD_LENGTH = 8


async def create_user(email: str, name: str, password: str, role: str, company_slug: str) -> str:
    async with AsyncSessionLocal() as db:
        company = await db.scalar(select(Company).where(Company.slug == company_slug))
        if not company:
            raise SystemExit(f"Empresa '{company_slug}' não encontrada.")
        email = email.strip().lower()
        user = await db.scalar(select(User).where(User.email == email))
        created = user is None
        if user is None:
            user = User(email=email, full_name=name, role=role, hashed_password=hash_password(password), status="active")
            db.add(user)
            await db.flush()
        else:
            user.full_name = name
            user.role = role
            user.status = "active"
            user.hashed_password = hash_password(password)
        link = await db.scalar(
            select(UserCompany).where(UserCompany.user_id == user.id, UserCompany.company_id == company.id)
        )
        if link is None:
            db.add(UserCompany(user_id=user.id, company_id=company.id))
        await db.commit()
        return f"{'Criado' if created else 'Atualizado'}: {email} ({role}) na empresa {company.name}."


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ou atualiza um usuário do painel.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=ROLES, default="vendas")
    parser.add_argument("--company", default="movfit", help="slug da empresa (padrão: movfit)")
    parser.add_argument("--password", help="se omitido, é pedida no terminal")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Senha: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    print(asyncio.run(create_user(args.email, args.name, password, args.role, args.company)))


if __name__ == "__main__":
    main()
