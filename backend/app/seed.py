import asyncio
import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import AiConfig, Company, Integration, User, UserCompany
from app.security import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed")


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Company).where(Company.slug == "movfit"))
        company = result.scalar_one_or_none()
        if not company:
            company = Company(name="Mov Fit", slug="movfit", plan="starter", status="active")
            db.add(company)
            await db.flush()
            logger.info("Empresa Mov Fit criada")
        else:
            logger.info("Empresa Mov Fit já existe")

        result = await db.execute(
            select(AiConfig).where(
                AiConfig.company_id == company.id,
                AiConfig.integration_id.is_(None),
            )
        )
        if not result.scalar_one_or_none():
            db.add(
                AiConfig(
                    company_id=company.id,
                    system_prompt=(
                        "Você é a Mônica, assistente virtual da Mov Fit. "
                        "Ajude com planos, horários e matrículas de forma cordial e objetiva."
                    ),
                    llm_provider="openai",
                    llm_model="gpt-4o-mini",
                )
            )

        result = await db.execute(select(User).where(User.email == "admin@movfit.com"))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                email="admin@movfit.com",
                full_name="Admin Mov Fit",
                role="super_admin",
                hashed_password=hash_password("admin123"),
                status="active",
            )
            db.add(user)
            await db.flush()
            db.add(UserCompany(user_id=user.id, company_id=company.id))
            logger.info("Usuário admin@movfit.com / admin123 criado")
        else:
            logger.info("Usuário seed já existe")

        result = await db.execute(
            select(Integration).where(
                Integration.company_id == company.id,
                Integration.adapter_key == "movfit_hub_v1",
            )
        )
        if not result.scalar_one_or_none():
            db.add(
                Integration(
                    company_id=company.id,
                    name="Mov Fit Hub",
                    integration_type="webhook",
                    adapter_key="movfit_hub_v1",
                    is_active=True,
                )
            )
            logger.info("Integração movfit_hub_v1 criada")

        await db.commit()
        logger.info("Seed concluído. Company ID: %s", company.id)


if __name__ == "__main__":
    asyncio.run(seed())
