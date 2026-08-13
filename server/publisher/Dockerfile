FROM ghcr.io/astral-sh/uv:0.8.15@sha256:2381d6aa60c326b71fd40023f921a0a3b8f91b14d5db6f7ea6dcd973971ce064 AS uv

FROM python:3.12.11-slim@sha256:6ee81689e4f2f243facdb89cdb5b8f19aacab42fc650525426211c9b0206477d AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml server/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY packages/policy/pyproject.toml packages/policy/pyproject.toml
COPY packages/telemetry/pyproject.toml packages/telemetry/pyproject.toml
COPY packages/testkit/pyproject.toml packages/testkit/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace
COPY server server
COPY packages packages
RUN uv sync --frozen --no-dev --package firekey-server

FROM python:3.12.11-slim@sha256:6ee81689e4f2f243facdb89cdb5b8f19aacab42fc650525426211c9b0206477d
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
RUN addgroup --system --gid 10001 firekey \
    && adduser --system --uid 10001 --ingroup firekey --no-create-home firekey
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "publisher.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
