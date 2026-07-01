from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/aquada_bdm"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


def get_database_url() -> str:
    """Return the PostgreSQL database URL from DATABASE_URL or a local default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine for PostgreSQL."""
    return create_engine(database_url or get_database_url(), echo=echo)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured SQLAlchemy session factory."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_tables(engine: Engine) -> None:
    """Create database tables for all registered ORM models."""
    # Import models so they are registered with Base.metadata before create_all().
    from aquada_bdm import models  # noqa: F401

    Base.metadata.create_all(engine)
