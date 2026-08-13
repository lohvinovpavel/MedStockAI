"""One database, one schema, eight processes.

Tenant isolation is row-level security, not a WHERE clause nobody forgets.
Every request opens a transaction and declares who it is acting as; RLS policies
read those settings. The app role has no BYPASSRLS and does not own the tables.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(hospital_id: str, actor_id: str):
    """Transaction-scoped tenant context. SET LOCAL dies with the transaction,
    so nothing leaks across pooled connections."""
    session: Session = SessionLocal()
    try:
        session.execute(
            text("SELECT set_config('app.hospital_id', :h, true), "
                 "set_config('app.actor_id', :a, true)"),
            {"h": hospital_id, "a": actor_id},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
