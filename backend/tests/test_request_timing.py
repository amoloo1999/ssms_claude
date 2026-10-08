import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.request_timing import RequestTimingMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/auth/callback")
    async def callback():
        return {"ok": True}

    @app.get("/assets/app.js")
    async def asset():
        return {"ok": True}

    return app


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def timing_lines():
    handler = _ListHandler()
    logger = logging.getLogger("sql_studio.timing")
    logger.addHandler(handler)
    yield handler.lines
    logger.removeHandler(handler)


def test_logs_path_status_and_duration_without_query_string(timing_lines):
    TestClient(_app()).get("/auth/callback?code=SECRET&state=xyz")
    assert len(timing_lines) == 1
    assert " GET /auth/callback 200 " in timing_lines[0]
    assert timing_lines[0].endswith("ms")
    assert "SECRET" not in timing_lines[0]


def test_skips_static_assets(timing_lines):
    TestClient(_app()).get("/assets/app.js")
    assert timing_lines == []
