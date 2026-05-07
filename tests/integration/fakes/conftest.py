"""Override parent autouse fixtures for fake-only tests.

The fakes here are pure in-memory helpers — they don't touch the database.
Overriding `clean_db` to a no-op prevents the Postgres container from
starting when only `tests/integration/fakes/` is run.
"""

from collections.abc import AsyncIterator

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def clean_db() -> AsyncIterator[None]:
    yield
