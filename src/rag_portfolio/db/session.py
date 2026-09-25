from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_portfolio.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"connect_timeout": 3, "options": "-c timezone=UTC"},
        )
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()
