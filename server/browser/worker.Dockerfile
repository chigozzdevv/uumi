FROM python:3.12.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.3 playwright==1.62.0 \
    && playwright install --with-deps --only-shell chromium \
    && pip uninstall --yes playwright \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY server ./server

RUN uv sync --frozen --no-dev --no-editable --package firekey-server --extra browser \
    && groupadd --gid 10001 firekey \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin firekey \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} +

USER 10001:10001

EXPOSE 8080

CMD ["uvicorn", "browser.workerapp:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
