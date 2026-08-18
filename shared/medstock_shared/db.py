"""One database, one schema, eight processes.

Tenant isolation is row-level security, not a WHERE clause nobody forgets.
Every request opens a transaction and declares who it is acting as; RLS policies
read those settings. The app role has no BYPASSRLS and does not own the tables.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

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


@contextmanager
def session_scope(hospital_id: str, actor_id: str, actor_system: str = ""):
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
                 "set_config('app.actor_system', :s, true)"),
            {"h": hospital_id, "a": actor_id or "", "s": actor_system or ""},
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
