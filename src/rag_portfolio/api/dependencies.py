from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as value:
        yield value
