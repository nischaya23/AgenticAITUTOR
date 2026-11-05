"""Exactly six tables. SQLite transactions stay short and never span an await."""
from datetime import datetime, timezone
from sqlalchemy import (JSON, DateTime, ForeignKey, Index, Integer, String,
                        create_engine, event, select)
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
        for operation in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS events_no_{operation.lower()}
                BEFORE {operation} ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END''')


def append_event(db, session_id: int, kind: str, data: dict) -> Event:
    from pydantic import TypeAdapter, JsonValue
    validated = TypeAdapter(dict[str, JsonValue]).validate_python(data)
    entry = Event(session_id=session_id, type=kind, data=validated)
    db.add(entry)
    db.flush()
    return entry


def local_user(db) -> User:
    user = db.scalar(select(User).where(User.email == 'student@local.test'))
    if not user:
        user = User(email='student@local.test')
        db.add(user)
        db.flush()
    return user
