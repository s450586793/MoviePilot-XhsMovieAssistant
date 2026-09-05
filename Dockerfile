FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir ".[test]" \
    && pytest -q

CMD ["sleep", "infinity"]

LABEL org.opencontainers.image.title="xhs-mp-bridge-phase1"
LABEL org.opencontainers.image.description="Xiaohongshu mentions probe using external CDP browser"
