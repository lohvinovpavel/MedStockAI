import os
from logging.config import fileConfig

from alembic import context
from medstock_shared.models import Base
from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# Seconds to wait for a TCP connection before giving up.
#
# Without this libpq waits on the kernel's SYN retry schedule, which is minutes
# on Linux and unbounded if a firewall blackholes the packets. That matters
# because migration runs as a Kubernetes Job under
# `kubectl wait --for=condition=complete`: an unreachable database produced no
# output, no error and no exit for the full timeout, and the only thing anyone
# ever saw was "timed out waiting for the condition" -- which is indistinguishable
# from a slow migration, a failed one, or a pod that never started.
#
# Ten seconds is far longer than a healthy connect inside a VPC and far shorter
# than the Job's budget, so an unreachable database now fails fast and says so.
CONNECT_TIMEOUT_SECONDS = 10


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    try:
        connection_ctx = connectable.connect()
    except OperationalError as exc:
        # The message libpq returns here names the actual cause -- timeout,
        # refused, no such host, authentication -- and it is worth far more to
        # whoever reads the Job log than a stack trace ending in `connect()`.
        raise SystemExit(
            f"cannot reach the database, so no migration ran: {exc.orig or exc}. "
            f"DATABASE_URL host/port unreachable within {CONNECT_TIMEOUT_SECONDS}s. "
            "Check the Cloud SQL instance is RUNNABLE and that the pod's network "
            "can reach it."
        ) from exc

    with connection_ctx as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
