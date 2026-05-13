"""Fixtures per integration test: Postgres effimero + isolamento per-test.

Tutti i test sotto tests/integration/ sono marcati `integration` (autouse).
Richiede Docker daemon attivo per testcontainers.
"""

import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

# Marca tutti i moduli sotto tests/integration/ come `integration`
pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Aggiunge il marker `integration` a tutti i test in tests/integration/."""
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """Avvia un container Postgres 16 effimero per la sessione di test."""
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    """URL Postgres sync (psycopg2) per Alembic."""
    return pg_container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    """Applica le migrazioni Alembic al container. Eseguita una volta per session."""
    os.environ["DATABASE_URL"] = database_url
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    return database_url


@pytest_asyncio.fixture(scope="session")
async def pg_pool(migrated_db: str) -> AsyncIterator[asyncpg.Pool]:
    """Pool asyncpg session-scoped (riusato come in produzione)."""
    pool = await asyncpg.create_pool(migrated_db, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(pg_pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Pulisce tutte le tabelle append-only prima di ogni test."""
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE markets, runners, market_snapshots, "
            "external_feature_snapshots, feature_vectors, config_snapshots, "
            "decisions, model_versions, model_inferences "
            "RESTART IDENTITY CASCADE"
        )
    yield
