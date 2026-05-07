"""Smoke test: il container Postgres parte e le migrazioni si applicano."""

import asyncpg


async def test_pool_can_query(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


async def test_schema_tables_exist(pg_pool: asyncpg.Pool):
    expected = {
        "markets",
        "runners",
        "market_snapshots",
        "external_feature_snapshots",
        "feature_vectors",
        "config_snapshots",
    }
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    found = {r["tablename"] for r in rows}
    assert expected.issubset(found), f"missing: {expected - found}"


async def test_truncate_isolation(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM markets")
    assert count == 0
