# One Dockerfile, eight images. Build with:
#   docker build --build-arg SERVICE=inventory -t medstock-inventory .
# Context is the repo root because every service imports shared/.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ARG SERVICE
ARG GIT_SHA=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 GIT_SHA=${GIT_SHA}

WORKDIR /srv
COPY pyproject.toml uv.loc[k] alembic.ini ./
COPY shared ./shared
COPY migrations ./migrations
COPY services/${SERVICE} ./services/${SERVICE}

RUN uv sync --package medstock-${SERVICE} --no-dev

ENV PATH="/srv/.venv/bin:$PATH"
WORKDIR /srv/services/${SERVICE}

RUN useradd --uid 10001 --no-create-home app
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
