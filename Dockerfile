FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENSUPPORT_DATABASE_URL=sqlite:////data/opensupport.db \
    UV_CACHE_DIR=/tmp/uv \
    UV_NO_CACHE=1 \
    PYTHONPATH=/srv/opensupport

WORKDIR /srv/opensupport

# Primero solo las dependencias: es la capa que cambia menos, así se cachea.
# --no-install-project porque el código todavía no está copiado; instalarlo acá
# produciría un paquete sin `app` y un ModuleNotFoundError al arrancar.
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN mkdir -p /data /tmp/uv \
    && useradd -r -u 1000 opensupport \
    && chown -R opensupport /data /tmp/uv /srv/opensupport
USER opensupport

EXPOSE 8000

# Se llama al módulo con el venv, no con `uv run`: uv quiere escribir en su
# cache del HOME y el usuario no-root no puede.
CMD ["sh", "-c", "/srv/opensupport/.venv/bin/python -m app.cli serve --host 0.0.0.0 --port ${PORT:-8000}"]
