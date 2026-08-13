.PHONY: lock sync format lint type test verify

UV_CACHE_DIR ?= .cache/uv
UV_PYTHON_INSTALL_DIR ?= .cache/python
UV_ENV = UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR)

lock:
	$(UV_ENV) uv lock

sync:
	$(UV_ENV) uv sync --all-packages --locked

format:
	$(UV_ENV) uv run ruff format .
	$(UV_ENV) uv run ruff check --fix .

lint:
	$(UV_ENV) uv run ruff format --check .
	$(UV_ENV) uv run ruff check .

type:
	$(UV_ENV) uv run mypy server packages

test:
	$(UV_ENV) uv run pytest

verify: lint type test
