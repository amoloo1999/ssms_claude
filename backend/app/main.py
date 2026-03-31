import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import init_db, async_session
from app.models import ServerConnection
from app.auth import router as auth_router
from app.routers.servers import router as servers_router
from app.routers.explorer import router as explorer_router
from app.routers.query import router as query_router
from app.routers.tables import router as tables_router
from app.routers.export import router as export_router

settings = get_settings()


async def seed_servers_from_config():
    """Load server connections from config.yaml on first startup."""
    try:
        with open(settings.config_file, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        return

    servers = config.get("servers") or []
    async with async_session() as db:
        for server_cfg in servers:
            # Skip if already exists (by host+port)
            result = await db.execute(
                select(ServerConnection).where(
                    ServerConnection.host == server_cfg["host"],
                    ServerConnection.port == server_cfg.get("port", 1433),
                    ServerConnection.from_config == True,
                )
            )
            if result.scalar_one_or_none():
                continue

            db.add(
                ServerConnection(
                    name=server_cfg["name"],
                    host=server_cfg["host"],
                    port=server_cfg.get("port", 1433),
                    username=server_cfg["username"],
                    password=server_cfg["password"],
                    description=server_cfg.get("description", ""),
                    from_config=True,
                )
            )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_servers_from_config()
    yield


app = FastAPI(
    title="SQL Studio",
    description="Web-based SQL Server Management Studio replacement",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400,  # 24 hours
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(servers_router)
app.include_router(explorer_router)
app.include_router(query_router)
app.include_router(tables_router)
app.include_router(export_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
