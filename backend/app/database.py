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
        # Migrations (idempotent — failures swallowed when column already exists)
        for migration in [
            "ALTER TABLE server_connections ADD COLUMN owner_email VARCHAR",
            "ALTER TABLE server_connections ADD COLUMN kind VARCHAR DEFAULT 'main'",
            # Multi-provider support: existing rows back-fill to 'mssql', so the
            # SQL Server behavior is unchanged for everything already seeded.
            "ALTER TABLE server_connections ADD COLUMN dialect VARCHAR DEFAULT 'mssql'",
            "ALTER TABLE server_connections ADD COLUMN database VARCHAR",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_server_host_port_config ON server_connections(host, port) WHERE from_config = 1",
        ]:
            try:
                await conn.execute(sqlalchemy.text(migration))
            except Exception:
                pass
