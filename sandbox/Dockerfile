FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/mia

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN python -m pip install --no-cache-dir ".[dev]" \
    && useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin mia

USER 10001:10001

ENTRYPOINT ["python", "-m", "mia.evolution.sandbox_worker"]
