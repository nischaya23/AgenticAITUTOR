"""SQLite models and additive migrations for existing local databases."""
from datetime import datetime, timezone
from sqlalchemy import (JSON, DateTime, ForeignKey, Index, Integer, String,
                        create_engine, event, inspect)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from . import config


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Login(Base):
    __tablename__ = 'logins'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class Material(Base):
    __tablename__ = 'materials'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('sessions.id'))
    type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('ix_events_session_order', 'session_id', 'id'),)


class StudentState(Base):
    __tablename__ = 'student_state'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Concept(Base):
    __tablename__ = 'concepts'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ConceptEdge(Base):
    __tablename__ = 'concept_edges'
    from_id: Mapped[int] = mapped_column(ForeignKey('concepts.id'), primary_key=True)
    to_id: Mapped[int] = mapped_column(ForeignKey('concepts.id'), primary_key=True)


class MemoryItem(Base):
    __tablename__ = 'memory_items'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Operation(Base):
    __tablename__ = 'operations'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default='running')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OperationEvent(Base):
    __tablename__ = 'operation_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey('operations.id'), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class WorkflowRun(Base):
    __tablename__ = 'workflow_runs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey('sessions.id'), nullable=True, index=True)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey('operations.id'), nullable=True, index=True)
    material_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowSpan(Base):
    __tablename__ = 'workflow_spans'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id'), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    consumed: Mapped[list] = mapped_column(JSON, default=list)
    produced: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowArtifact(Base):
    __tablename__ = 'workflow_artifacts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowEvent(Base):
    __tablename__ = 'workflow_events'
    cursor: Mapped[int] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(32), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id'), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


engine = create_engine(config.DATABASE_URL, connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine, 'connect')
def sqlite_setup(connection, _):
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=5000')
    connection.execute('PRAGMA journal_mode=WAL')


def init_db():
    Base.metadata.create_all(engine)
    # DB-level append-only guarantees also cover direct SQL, not just ORM use.
    with engine.begin() as connection:
        columns = {column['name'] for column in inspect(connection).get_columns('users')}
        if 'password_hash' not in columns:
            connection.exec_driver_sql('ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)')
        for table in ('workflow_events', 'workflow_artifacts'):
            for operation in ('UPDATE', 'DELETE'):
                connection.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '{table} are immutable'); END''')
        for operation in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS events_no_{operation.lower()}
                BEFORE {operation} ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END''')


def append_event(db, session_id: int, kind: str, data: dict) -> Event:
    from pydantic import TypeAdapter, JsonValue
    validated = TypeAdapter(dict[str, JsonValue]).validate_python(data)
    entry = Event(session_id=session_id, type=kind, data=validated)
    db.add(entry)
    db.flush()
    from . import workflow
    workflow.link_event(db, entry)
    return entry
