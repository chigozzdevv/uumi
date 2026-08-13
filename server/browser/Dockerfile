FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN python -m venv /opt/venv

COPY packages /app/packages
COPY server /app/server
COPY pyproject.toml uv.lock /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --python /opt/venv/bin/python --no-cache /app/server

WORKDIR /app/server

RUN useradd --create-home --uid 10001 firekey && chown -R firekey:firekey /app

USER firekey

CMD ["python", "-m", "browser.gatewaymain"]
