# One Dockerfile, eight images. Build with:
#   docker build --build-arg SERVICE=inventory -t medstock-inventory .
# Context is the repo root because every service imports shared/.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1

WORKDIR /srv
COPY pyproject.toml uv.loc[k] ./
COPY shared ./shared
COPY services/${SERVICE} ./services/${SERVICE}

RUN uv sync --package medstock-${SERVICE} --no-dev

ENV PATH="/srv/.venv/bin:$PATH"
WORKDIR /srv/services/${SERVICE}

RUN useradd --uid 10001 --no-create-home app
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
