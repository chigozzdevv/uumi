.PHONY: lock sync format image publisher-image lint type test infra verify

UV_CACHE_DIR ?= .cache/uv
UV_PYTHON_INSTALL_DIR ?= .cache/python
UV_ENV = UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR)
TERRAFORM ?= terraform
TF_CACHE_DIR ?= $(CURDIR)/.cache/terraform

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

image:
	docker build --file server/api/Dockerfile --tag firekey-api:local .

publisher-image:
	docker build --file server/publisher/Dockerfile --tag firekey-publisher:local .

infra:
	mkdir -p $(TF_CACHE_DIR)
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) fmt -check -recursive infra
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/bootstrap init -backend=false -input=false
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/bootstrap validate
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/environments/dev init -backend=false -input=false
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/environments/dev validate
