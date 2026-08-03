from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    DateTime,
    Boolean,
    UniqueConstraint,
    Index,
)
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import Optional, Literal
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
    # 'main' = available to all authenticated users (view-only for non-RevMan).
    # 'gp'   = hidden from non-RevMan users entirely.
    kind = Column(String, default="main", nullable=False)
    # Database engine: 'mssql' (default, back-compat), 'postgres', 'mysql',
    # 'snowflake'. Drives which driver in app.services.drivers handles this
    # server's connections + introspection.
    dialect = Column(String, default="mssql", nullable=False)
    # The single database a connection binds to for engines that connect to one
    # database at a time (Postgres / MySQL / Snowflake). Ignored for MSSQL,
    # which switches databases per query via the connection string.
    database = Column(String, nullable=True)
    # Per-server write policy, independent of the caller's role:
    #   'read_write' — writes allowed for users whose role permits them
    #   'read_only'  — writes refused for EVERYONE, RevMan included
    # This is how "Aurora is read-only for all users" is expressed. The Aurora
    # connection also authenticates as a read-only database user, so this is a
    # second layer rather than the only one — but it means the app refuses the
    # statement instead of sending it and surfacing a driver error.
    write_policy = Column(String, default="read_write", nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TablePermission(Base):
    __tablename__ = "table_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_email", "server_id", "database", "schema_name", "table_name",
            name="uq_table_perm",
        ),
        Index("ix_table_perm_user", "user_email"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String, nullable=False)
    server_id = Column(Integer, nullable=False)
    database = Column(String, nullable=False)
    schema_name = Column(String, nullable=False, default="dbo")
    table_name = Column(String, nullable=False)
    granted_by = Column(String, nullable=False)
    granted_at = Column(DateTime, server_default=func.now())


class AccessRequest(Base):
    __tablename__ = "access_requests"
    __table_args__ = (
        Index("ix_access_req_status", "status"),
        Index("ix_access_req_user", "user_email"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String, nullable=False)
    server_id = Column(Integer, nullable=False)
    database = Column(String, nullable=False)
    schema_name = Column(String, nullable=False, default="dbo")
    table_name = Column(String, nullable=False)
    reason = Column(String, default="")
    status = Column(String, default="pending", nullable=False)  # pending|approved|denied
    created_at = Column(DateTime, server_default=func.now())
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(String, default="")


class QueryHistory(Base):
    """One row per execution, written after the permission check passes.

    Scoped to the user who ran it and never shared: the SQL text names objects,
    and showing one person's history to another would leak the existence and
    shape of tables they have no grant for. Failed runs are kept — "what was
    that query that errored yesterday" is most of why history is useful.
    """

    __tablename__ = "query_history"
    __table_args__ = (
        # The only query this table serves: a user's own runs, newest first.
        Index("ix_query_history_user_time", "user_email", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String, nullable=False)
    server_id = Column(Integer, nullable=False)
    server_name = Column(String, default="")
    database = Column(String, default="")
    sql = Column(Text, nullable=False)
    status = Column(String, default="ok", nullable=False)  # ok | error
    row_count = Column(Integer, default=0)
    duration_ms = Column(Float, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, server_default=func.now())


class Snippet(Base):
    """A saved, optionally shared, named query."""

    __tablename__ = "snippets"
    __table_args__ = (Index("ix_snippet_owner", "owner_email"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    sql = Column(Text, nullable=False)
    description = Column(String, default="")
    is_shared = Column(Boolean, default=False, nullable=False)
    use_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditEvent(Base):
    """An append-only record of consequential actions.

    Only ever inserted, never updated or deleted through the API — an audit log
    an actor can edit is not an audit log. ``actor`` is a string rather than a
    user id because not every actor is a person: a scheduled query records
    itself as its owner with actor_kind='schedule'.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_at", "at"),
        Index("ix_audit_type", "event_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(DateTime, server_default=func.now())
    actor = Column(String, nullable=False)
    actor_kind = Column(String, default="user", nullable=False)  # user | schedule
    event_type = Column(String, nullable=False)  # write | export | grant | deny | kill | denied
    server_id = Column(Integer, nullable=True)
    server_name = Column(String, default="")
    database = Column(String, default="")
    detail = Column(Text, default="")
    reason = Column(Text, default="")
    result = Column(String, default="ok")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    picture = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime, server_default=func.now())


# -- Pydantic schemas --


Dialect = Literal["mssql", "postgres", "mysql", "snowflake"]
WritePolicy = Literal["read_write", "read_only"]


class ServerConnectionCreate(BaseModel):
    name: str
    host: str
    port: int = 1433
    username: str
    password: str
    description: str = ""
    kind: Literal["main", "gp"] = "main"
    dialect: Dialect = "mssql"
    # Required for single-database engines (postgres/mysql/snowflake); ignored
    # for mssql.
    database: Optional[str] = None
    write_policy: WritePolicy = "read_write"


class ServerConnectionUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    kind: Optional[Literal["main", "gp"]] = None
    dialect: Optional[Dialect] = None
    database: Optional[str] = None
    write_policy: Optional[WritePolicy] = None


class ServerConnectionResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    description: str
    from_config: bool
    owner_email: Optional[str] = None
    kind: str = "main"
    dialect: str = "mssql"
    database: Optional[str] = None
    write_policy: str = "read_write"
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QueryRequest(BaseModel):
    server_id: int
    database: str
    sql: str
    # Client-generated id (UUID) used to cancel this query mid-flight via the
    # Stop button. Optional so older clients / API callers keep working.
    query_id: Optional[str] = None


class CancelRequest(BaseModel):
    query_id: str


class ResultSet(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    result_sets: list[ResultSet] = []
    execution_time_ms: float
    error: Optional[str] = None


class AIGenerateRequest(BaseModel):
    server_id: int
    database: str
    prompt: str
    current_sql: Optional[str] = None


class AIFixRequest(BaseModel):
    server_id: int
    database: str
    sql: str
    error: str


class AIFindDataRequest(BaseModel):
    server_id: int
    prompt: str


class AIResponse(BaseModel):
    sql: Optional[str] = None
    explanation: str
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
    role: Literal["revman", "user"] = "user"
    is_approver: bool = False
    # Exempt from a connection's read_only policy (WRITE_ANYWHERE_EMAILS).
    can_write_anywhere: bool = False

    class Config:
        from_attributes = True


# -- Permission API schemas --


class TablePermissionResponse(BaseModel):
    id: int
    user_email: str
    server_id: int
    database: str
    schema_name: str
    table_name: str
    granted_by: str
    granted_at: datetime

    class Config:
        from_attributes = True


class TablePermissionCreate(BaseModel):
    user_email: str
    server_id: int
    # 'table' = single table; 'database' = every table in this DB; 'server' = entire server.
    scope: Literal["table", "database", "server"] = "table"
    database: str = "*"
    schema_name: str = "dbo"
    table_name: str = "*"


class AccessRequestCreate(BaseModel):
    server_id: int
    scope: Literal["table", "database", "server"] = "table"
    database: str = "*"
    schema_name: str = "dbo"
    table_name: str = "*"
    reason: str = ""


class AccessRequestResponse(BaseModel):
    id: int
    user_email: str
    server_id: int
    database: str
    schema_name: str
    table_name: str
    reason: str
    status: str
    created_at: datetime
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_note: str = ""

    class Config:
        from_attributes = True


class AccessRequestDecision(BaseModel):
    note: str = ""


# -- History / snippets --


class QueryHistoryResponse(BaseModel):
    id: int
    server_id: int
    server_name: str = ""
    database: str = ""
    sql: str
    status: str
    row_count: int = 0
    duration_ms: float = 0
    error: Optional[str] = None
    started_at: datetime

    class Config:
        from_attributes = True


class SnippetCreate(BaseModel):
    name: str
    sql: str
    description: str = ""
    is_shared: bool = False


class SnippetUpdate(BaseModel):
    name: Optional[str] = None
    sql: Optional[str] = None
    description: Optional[str] = None
    is_shared: Optional[bool] = None


class AuditEventResponse(BaseModel):
    id: int
    at: datetime
    actor: str
    actor_kind: str = "user"
    event_type: str
    server_id: Optional[int] = None
    server_name: str = ""
    database: str = ""
    detail: str = ""
    reason: str = ""
    result: str = "ok"

    class Config:
        from_attributes = True


class KillSessionRequest(BaseModel):
    server_id: int
    session_id: int
    # Required and non-trivial: killing a session rolls back someone's
    # in-flight transaction, and the log is worthless without the why.
    reason: str


class SnippetResponse(BaseModel):
    id: int
    owner_email: str
    name: str
    sql: str
    description: str = ""
    is_shared: bool = False
    use_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
