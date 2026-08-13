FROM python:3.12.13-slim-bookworm

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

RUN useradd --create-home --uid 10001 firekey

USER firekey

EXPOSE 8080

CMD ["uvicorn", "browser.gatewayapp:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
