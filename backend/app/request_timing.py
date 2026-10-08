"""Timestamped per-request timing log.

uvicorn's access log has no timestamps, so the service log on MSSQL01 can't
answer "when did this request arrive, and how long did it take?" -- a slow
login on 2026-10-08 could only be ordered by the office NAT's source ports.
This writes one line per request with the arrival time and the duration.

Only the path is logged, never the query string: /auth/callback carries the
OAuth code and state there. Static frontend assets are skipped as noise.

Pure ASGI rather than BaseHTTPMiddleware, which buffers streaming responses
(exports) and breaks client-disconnect handling.
"""

import logging
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("sql_studio.timing")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_TIMED_PREFIXES = ("/api/", "/auth/", "/health")


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(_TIMED_PREFIXES):
            await self.app(scope, receive, send)
            return

        arrived = datetime.now(timezone.utc)
        start = time.perf_counter()
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            client = scope.get("client")
            logger.info(
                "TIMING %s %s %s %s %d %.0fms",
                arrived.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                f"{client[0]}:{client[1]}" if client else "-",
                scope["method"],
                scope["path"],
                status or 500,
                (time.perf_counter() - start) * 1000,
            )
