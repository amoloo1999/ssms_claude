"""Driver registry: dialect string → cached ``DatabaseDriver`` instance.

Drivers are built lazily and memoized. The Snowflake driver in particular pulls
a heavy connector (pyarrow), so its module is only imported the first time a
Snowflake server is actually used — the app boots fine without the wheel
installed.
"""

from __future__ import annotations

from typing import Callable

from app.services.drivers.base import DatabaseDriver

_instances: dict[str, DatabaseDriver] = {}


def _build_mssql() -> DatabaseDriver:
    from app.services.drivers.mssql import MssqlDriver

    return MssqlDriver()


def _build_postgres() -> DatabaseDriver:
    from app.services.drivers.postgres import PostgresDriver

    return PostgresDriver()


def _build_mysql() -> DatabaseDriver:
    from app.services.drivers.mysql import MysqlDriver

    return MysqlDriver()


def _build_snowflake() -> DatabaseDriver:
    from app.services.drivers.snowflake import SnowflakeDriver

    return SnowflakeDriver()


_FACTORIES: dict[str, Callable[[], DatabaseDriver]] = {
    "mssql": _build_mssql,
    "postgres": _build_postgres,
    "mysql": _build_mysql,
    "snowflake": _build_snowflake,
}

# Aliases so config/UI can use friendlier names.
_ALIASES = {
    "sqlserver": "mssql",
    "mssqlserver": "mssql",
    "postgresql": "postgres",
    "pg": "postgres",
    "aurora-postgres": "postgres",
    "aurora-postgresql": "postgres",
    "aurora-mysql": "mysql",
    "mariadb": "mysql",
}


def get_driver(dialect: str | None) -> DatabaseDriver:
    key = (dialect or "mssql").lower()
    key = _ALIASES.get(key, key)
    if key not in _FACTORIES:
        raise ValueError(f"Unsupported database dialect: {dialect!r}")
    if key not in _instances:
        _instances[key] = _FACTORIES[key]()
    return _instances[key]


def supported_dialects() -> list[str]:
    return list(_FACTORIES.keys())
