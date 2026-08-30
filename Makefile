.PHONY: lock sync format lint type test client infra verify image images api-image publisher-image ingestion-image broker-image coordinator-image browser-image gateway-image notification-image auditlog-image web-image demo-image

UV_CACHE_DIR ?= .cache/uv
UV_PYTHON_INSTALL_DIR ?= .cache/python
UV_ENV = UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR)
TERRAFORM ?= terraform
TF_CACHE_DIR ?= $(CURDIR)/.cache/terraform

lock:
	$(UV_ENV) uv lock

sync:
	$(UV_ENV) uv sync --all-packages --all-extras --locked
	npm --prefix client ci

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

client:
	npm --prefix client run check

verify: lint type test client

image: api-image

images: api-image publisher-image ingestion-image broker-image coordinator-image browser-image gateway-image notification-image auditlog-image web-image demo-image

api-image:
	docker build --file server/api/Dockerfile --tag uumi-api:local .

web-image:
	docker build --file server/web/Dockerfile --tag uumi-web:local .

demo-image:
	docker build --file server/demo/Dockerfile --tag uumi-demo:local .

publisher-image:
	docker build --file server/publisher/Dockerfile --tag uumi-publisher:local .

ingestion-image:
	docker build --file server/ingestion/Dockerfile --tag uumi-ingestion:local .

broker-image:
	docker build --file server/broker/Dockerfile --tag uumi-broker:local .

coordinator-image:
	docker build --file server/coordinator/Dockerfile --tag uumi-coordinator:local .

browser-image:
	docker build --file server/browser/worker.Dockerfile --tag uumi-browser:local .

gateway-image:
	docker build --file server/browser/Dockerfile --tag uumi-gateway:local .

notification-image:
	docker build --file server/notification/Dockerfile --tag uumi-notification:local .

auditlog-image:
	docker build --file server/auditlog/Dockerfile --tag uumi-auditlog:local .

infra:
	mkdir -p $(TF_CACHE_DIR)
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) fmt -check -recursive infra
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/bootstrap init -backend=false -input=false
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/bootstrap validate
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/environments/dev init -backend=false -input=false
	TF_PLUGIN_CACHE_DIR=$(TF_CACHE_DIR) $(TERRAFORM) -chdir=infra/terraform/environments/dev validate
