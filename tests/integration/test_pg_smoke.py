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
        "decisions",
    }
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    found = {r["tablename"] for r in rows}
    assert expected.issubset(found), f"missing: {expected - found}"


async def test_clean_db_first_inserts_visible(pg_pool: asyncpg.Pool):
    """Insert a marker row; the second test must NOT see it."""
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO markets (market_id, event_id, sport_id, market_type, start_time) "
            "VALUES ($1, $2, '1', 'MATCH_ODDS', NOW())",
            "1.SMOKE",
            "EVT-SMOKE",
        )
        count = await conn.fetchval("SELECT COUNT(*) FROM markets")
    assert count == 1


async def test_clean_db_second_sees_empty_table(pg_pool: asyncpg.Pool):
    """If autouse clean_db ran, the row from the previous test is gone."""
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM markets")
    assert count == 0
