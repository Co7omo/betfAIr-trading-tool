import asyncpg
import structlog
from aiohttp import web

log = structlog.get_logger()


async def health_handler(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["db_pool"]
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return web.json_response({"status": "healthy"})
    except Exception as e:
        log.error("health_check_failed", error=str(e))
        return web.json_response({"status": "unhealthy", "error": str(e)}, status=503)


async def start_health_server(pool: asyncpg.Pool, port: int = 8080) -> web.AppRunner:
    app = web.Application()
    app["db_pool"] = pool
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("health_server_started", port=port)
    return runner
