FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.3

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY server ./server

RUN uv sync --frozen --no-dev --no-editable --package firekey-server \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} +

USER pwuser

CMD ["uvicorn", "browser.workerapp:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
