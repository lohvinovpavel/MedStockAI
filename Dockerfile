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
# Demo seeding runs as a one-off Job in-cluster (deploy/k8s/seed-stock-job.yaml),
# so the scripts have to be in the image. Same reasoning as migrations above:
# any image can run them, none runs them on startup.
COPY scripts ./scripts
# seed_demo (deploy/k8s/seed-demo-job.yaml, ingest image) reads these
# committed artifacts at services/ingest/app/demo_layout.py's data_dir() --
# same one-off-Job reasoning as scripts/migrations just above. ~2.3MB, every
# image, cheaper than a ConfigMap (etcd's ~1MiB object limit is smaller than
# consumption.csv.gz alone).
COPY data ./data
COPY services/${SERVICE} ./services/${SERVICE}

RUN uv sync --package medstock-${SERVICE} --no-dev

ENV PATH="/srv/.venv/bin:$PATH"
WORKDIR /srv/services/${SERVICE}

RUN useradd --uid 10001 --no-create-home app
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
