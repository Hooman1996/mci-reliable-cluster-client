PYTHON ?= python
UV ?= uv
IMAGE ?= mci-cluster-client:local
DOCKER ?= docker
KUBECTL ?= kubectl
COVERAGE_ARGS ?= --cov=mci_cluster_client --cov-branch --cov-report=term-missing

.PHONY: help lock-check format format-check lint typecheck test coverage verify cli-smoke \
	docker-check docker-build docker-smoke k8s-render

help: ## Show the available developer commands.
	printf '%s\n' \
		'help          Show the available developer commands.' \
		'lock-check    Check uv.lock against pyproject.toml without network or installs.' \
		'format        Format Python sources and tests with Ruff.' \
		'format-check  Check formatting without changing files.' \
		'lint          Run Ruff lint checks.' \
		'typecheck     Run strict Mypy checks over src/.' \
		'test          Run the deterministic test suite.' \
		'coverage      Run branch coverage (override COVERAGE_ARGS as needed).' \
		'verify        Run non-mutating Python quality gates and CLI smoke tests.' \
		'cli-smoke     Exercise only offline CLI help and version paths.' \
		'docker-check  Run the Dockerfile BuildKit validation check.' \
		'docker-build  Build and load IMAGE (default: mci-cluster-client:local).' \
		'docker-smoke  Inspect and smoke-test an already-built IMAGE offline.' \
		'k8s-render    Render the Kubernetes manifests offline with Kustomize.'

lock-check: ## Check the committed lock without network access or synchronization.
	$(UV) lock --check --offline

format: ## Format Python sources and tests with Ruff.
	$(PYTHON) -m ruff format .

format-check: ## Check formatting without changing files.
	$(PYTHON) -m ruff format --check .

lint: ## Run Ruff lint checks.
	$(PYTHON) -m ruff check .

typecheck: ## Run strict Mypy checks over src/.
	$(PYTHON) -m mypy src

test: ## Run the deterministic test suite.
	$(PYTHON) -m pytest

coverage: ## Run branch-aware coverage with the configured threshold.
	$(PYTHON) -m pytest $(COVERAGE_ARGS)

verify: format-check lint typecheck coverage cli-smoke ## Run all non-mutating Python checks.

cli-smoke: ## Exercise CLI paths that cannot contact or mutate a cluster.
	PYTHONPATH=src $(PYTHON) -m mci_cluster_client --help
	PYTHONPATH=src $(PYTHON) -m mci_cluster_client --version
	PYTHONPATH=src $(PYTHON) -m mci_cluster_client create --help
	PYTHONPATH=src $(PYTHON) -m mci_cluster_client delete --help

docker-check: ## Validate the Dockerfile with BuildKit without building it.
	$(DOCKER) buildx build --check .

docker-build: ## Build and load the local container image without publishing it.
	$(DOCKER) buildx build --load --tag $(IMAGE) .

docker-smoke: ## Inspect and exercise only offline, non-mutating image paths.
	$(DOCKER) run --rm --network none $(IMAGE)
	$(DOCKER) run --rm --network none $(IMAGE) --version
	$(DOCKER) run --rm --network none $(IMAGE) create --help
	$(DOCKER) run --rm --network none $(IMAGE) delete --help
	$(DOCKER) run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
		--network none $(IMAGE) --help
	$(DOCKER) image inspect --format 'user={{json .Config.User}}' $(IMAGE)
	$(DOCKER) image inspect --format 'entrypoint={{json .Config.Entrypoint}}' $(IMAGE)
	$(DOCKER) image inspect --format 'cmd={{json .Config.Cmd}}' $(IMAGE)
	$(DOCKER) image inspect --format 'size_bytes={{.Size}}' $(IMAGE)
	test "$$($(DOCKER) image inspect --format '{{.Config.User}}' $(IMAGE))" = '10001:10001'
	test "$$($(DOCKER) image inspect --format '{{json .Config.Entrypoint}}' $(IMAGE))" = '["mci-cluster"]'
	test "$$($(DOCKER) image inspect --format '{{json .Config.Cmd}}' $(IMAGE))" = '["--help"]'
	$(DOCKER) run --rm --network none --entrypoint python $(IMAGE) -c \
		"import importlib.util as u; assert u.find_spec('httpx') is not None; assert all(u.find_spec(name) is None for name in ('pytest', 'coverage', 'mypy', 'ruff'))"

k8s-render: ## Render manifests locally; never contacts or mutates a cluster.
	$(KUBECTL) kustomize manifests
