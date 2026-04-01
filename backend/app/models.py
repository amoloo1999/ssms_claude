from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.database import Base


# -- SQLAlchemy ORM models (SQLite app database) --


class ServerConnection(Base):
    __tablename__ = "server_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    host = Column(String, nullable=False)
    port = Column(Integer, default=1433)
    username = Column(String, nullable=False)
    password = Column(String, nullable=False)  # encrypted in production
    description = Column(String, default="")
    from_config = Column(Boolean, default=False)
    owner_email = Column(String, nullable=True)  # null = shared (from config)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    picture = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime, server_default=func.now())


# -- Pydantic schemas --


class ServerConnectionCreate(BaseModel):
    name: str
    host: str
    port: int = 1433
    username: str
    password: str
    description: str = ""


class ServerConnectionUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None


class ServerConnectionResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    description: str
    from_config: bool
    owner_email: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QueryRequest(BaseModel):
    server_id: int
    database: str
    sql: str


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_time_ms: float
    error: Optional[str] = None


class TableEditRequest(BaseModel):
    server_id: int
    database: str
    schema_name: str = "dbo"
    table: str
    primary_key_columns: list[str]
    primary_key_values: list
    column: str
    new_value: Optional[str] = None


class ExportRequest(BaseModel):
    server_id: int
    database: str
    sql: str
    format: str = "csv"  # csv or xlsx


class UserResponse(BaseModel):
    email: str
    name: str
    picture: str

    class Config:
        from_attributes = True
