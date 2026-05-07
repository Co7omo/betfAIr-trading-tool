import asyncio

import betfairlightweight
import structlog

from betfair_trading.settings import Settings

log = structlog.get_logger()


def create_betfair_client(settings: Settings) -> betfairlightweight.APIClient:
    client = betfairlightweight.APIClient(
        username=settings.betfair_username,
        password=settings.betfair_password,
        app_key=settings.betfair_app_key,
        certs=settings.betfair_cert_path,
    )
    return client


async def login(client: betfairlightweight.APIClient) -> None:
    await asyncio.to_thread(client.login)
    log.info("betfair_login_success")


async def keep_alive(client: betfairlightweight.APIClient) -> None:
    await asyncio.to_thread(client.keep_alive)
    log.debug("betfair_keep_alive")


async def logout(client: betfairlightweight.APIClient) -> None:
    await asyncio.to_thread(client.logout)
    log.info("betfair_logout")
