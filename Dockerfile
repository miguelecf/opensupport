FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENSUPPORT_DATABASE_URL=sqlite:////data/opensupport.db

WORKDIR /srv/opensupport

COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN mkdir -p /data && useradd -r -u 1000 opensupport && chown -R opensupport /data /srv/opensupport
USER opensupport

EXPOSE 8000
CMD ["sh", "-c", "uv run opensupport serve --host 0.0.0.0 --port ${PORT:-8000}"]
