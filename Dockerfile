FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir --no-build-isolation ".[test]" \
    && pytest -q

CMD ["sleep", "infinity"]

LABEL org.opencontainers.image.title="xhs-mp-bridge-phase3"
LABEL org.opencontainers.image.description="Xiaohongshu mention capture and idempotent request storage"
