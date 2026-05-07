import asyncpg
import structlog

log = structlog.get_logger()


async def create_pool(database_url: str) -> asyncpg.Pool:
    # asyncpg needs postgresql:// not postgresql+asyncpg://
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
    log.info("db_pool_created", min_size=2, max_size=10)
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()
    log.info("db_pool_closed")
