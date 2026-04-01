import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations
        for migration in [
            "ALTER TABLE server_connections ADD COLUMN owner_email VARCHAR",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_server_host_port_config ON server_connections(host, port) WHERE from_config = 1",
        ]:
            try:
                await conn.execute(sqlalchemy.text(migration))
            except Exception:
                pass
