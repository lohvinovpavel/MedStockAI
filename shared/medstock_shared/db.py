"""One database, one schema, eight processes.

Tenant isolation is row-level security, not a WHERE clause nobody forgets.
Every request opens a transaction and declares who it is acting as; RLS policies
read those settings. The app role has no BYPASSRLS and does not own the tables.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

# H2: ask_ai() stamps this so a later session_scope in the same request
# copies the key onto audit_log_entry via the H1 trigger.
_ai_dedupe_key: ContextVar[str] = ContextVar("app_ai_dedupe_key", default="")

# connect_timeout: formulary overlay is best-effort. An unreachable DB must
# fail in seconds (SQLAlchemyError → empty formulary), not hang until Next's
# rewrite proxy returns 500 Internal Server Error.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=5,
    connect_args={"connect_timeout": 5},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def bind_ai_dedupe_key(key: str | None) -> None:
    """Remember the current ask_ai key for the rest of this request."""
    _ai_dedupe_key.set(key or "")


def set_ai_dedupe_key(session: Session, key: str | None) -> None:
    """H2: stamp the surrounding transaction so H1's trigger copies the key."""
    bind_ai_dedupe_key(key)
    session.execute(
        text("SELECT set_config('app.ai_dedupe_key', :k, true)"),
        {"k": key or ""},
    )


def iter_hospitals(session: Session) -> Iterator[str]:
    """Yield each hospital id, setting `app.hospital_id` for the rest of the
    transaction before each yield so a per-tenant query right after sees only
    that hospital's rows under RLS.

    For batch/ingest code that needs to scan every tenant's data rather than
    one request's — callers loop `for hid in iter_hospitals(session): ...`.
    """
    from .models import Hospital  # local import: models.py does not import db.py

    for hid in session.scalars(select(Hospital.id)).all():
        session.execute(
            text("SELECT set_config('app.hospital_id', :h, true)"),
            {"h": str(hid)},
        )
        yield str(hid)


@contextmanager
def session_scope(
    hospital_id: str,
    actor_id: str,
    actor_system: str = "",
    ai_dedupe_key: str = "",
):
    """Transaction-scoped tenant context. SET LOCAL dies with the transaction,
    so nothing leaks across pooled connections.

    `actor_system` is how a CronJob attributes a write (H1): pass the pipeline
    name and leave `actor_id` empty. A write with neither set fails the
    `audit_log_entry` CHECK and rolls back the business transaction.
    """
    session: Session = SessionLocal()
    try:
        session.execute(
            text("SELECT set_config('app.hospital_id', :h, true), "
                 "set_config('app.actor_id', :a, true), "
                 "set_config('app.actor_system', :s, true), "
                 "set_config('app.ai_dedupe_key', :k, true)"),
            {
                "h": hospital_id,
                "a": actor_id or "",
                "s": actor_system or "",
                "k": ai_dedupe_key or _ai_dedupe_key.get() or "",
            },
        )
        # Docker/CI connect as a superuser that would otherwise bypass FORCE
        # RLS (A4). SET LOCAL dies with the transaction, same as the GUCs.
        session.execute(text("SET LOCAL ROLE app_role"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
