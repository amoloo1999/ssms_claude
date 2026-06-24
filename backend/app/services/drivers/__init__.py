"""Pluggable database drivers.

Public surface:
    get_driver(dialect)   -> DatabaseDriver   (cached per dialect)
    supported_dialects()  -> list[str]
    DatabaseDriver, ConnHandle
"""

from app.services.drivers.base import ConnHandle, DatabaseDriver
from app.services.drivers.registry import get_driver, supported_dialects

__all__ = [
    "ConnHandle",
    "DatabaseDriver",
    "get_driver",
    "supported_dialects",
]
