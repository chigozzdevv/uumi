FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

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

USER pwuser

CMD ["python", "-m", "browser.workermain"]
