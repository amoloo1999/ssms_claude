from pathlib import Path

import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse
from sqlalchemy import select

from app.config import get_settings
from app.database import init_db, async_session
from app.models import ServerConnection
from app.services.drivers import get_driver
from app.auth import router as auth_router
from app.routers.servers import router as servers_router
from app.routers.explorer import router as explorer_router
from app.routers.query import router as query_router
from app.routers.tables import router as tables_router
from app.routers.export import router as export_router
from app.routers.ai import router as ai_router
from app.routers.permissions import router as permissions_router
from app.routers.history import router as history_router
from app.routers.ops import router as ops_router
from app.routers.schedules import router as schedules_router

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
            dialect = server_cfg.get("dialect", "mssql")
            # Default port follows the engine when the config omits it.
            port = server_cfg.get("port", get_driver(dialect).default_port)
            # Skip if already exists (by host+port)
            result = await db.execute(
                select(ServerConnection).where(
                    ServerConnection.host == server_cfg["host"],
                    ServerConnection.port == port,
                    ServerConnection.from_config == True,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Already seeded. Re-assert the write policy ONLY when config
                # states one explicitly. Defaulting here instead would clobber a
                # policy set through the UI back to read_write on every restart,
                # which would mean the only way to mark a connection read-only
                # was to edit this file on the production box.
                if "write_policy" in server_cfg:
                    existing.write_policy = server_cfg["write_policy"]
                continue

            db.add(
                ServerConnection(
                    name=server_cfg["name"],
                    host=server_cfg["host"],
                    port=port,
                    username=server_cfg["username"],
                    password=server_cfg["password"],
                    description=server_cfg.get("description", ""),
                    kind=server_cfg.get("kind", "main"),
                    dialect=dialect,
                    database=server_cfg.get("database"),
                    write_policy=server_cfg.get("write_policy", "read_write"),
                    from_config=True,
                )
            )
        try:
            await db.commit()
        except Exception:
            await db.rollback()  # another worker already seeded


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
app.include_router(ai_router)
app.include_router(permissions_router)
app.include_router(history_router)
app.include_router(ops_router)
app.include_router(schedules_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Serve built frontend (production). Must be mounted AFTER all API routes so
# API paths aren't shadowed by the SPA catch-all. StaticFiles' html=True only
# serves index.html for directory paths, so we subclass it to also fall back
# on missing files — that's what makes client-side routes like /login work
# when the user hits them directly instead of getting FastAPI's 404 JSON.
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


_API_PREFIXES = ("/api/", "/auth/", "/health")


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Don't mask real API 404s with the SPA shell — only fall back
            # for client-side routes. Check scope["path"] (the URL, always
            # forward-slashed) rather than `path`, which Starlette builds
            # via os.path.join and so uses backslashes on Windows.
            url_path = scope.get("path", "")
            if (
                exc.status_code == 404
                and scope["method"] == "GET"
                and not url_path.startswith(_API_PREFIXES)
            ):
                return FileResponse(Path(self.directory) / "index.html")
            raise


if _frontend_dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
