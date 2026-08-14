import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import FastAPI
from medstock_shared import engine
from sqlalchemy import text

app = FastAPI(title="prediction")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-prediction package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-prediction")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "prediction", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}
