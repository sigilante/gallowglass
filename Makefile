# Gallowglass — top-level Makefile

PYTHON := python3
DOCKER_IMAGE := gallowglass-dev

.PHONY: test test-ci test-harness test-plan test-seed test-bootstrap \
        test-compiler test-selfhost test-selfhost-docker \
        test-demos demo-glass-ir \
        vendor vendor-verify \
        docker-build _docker-ensure clean help

## Populate vendor/ at SHAs pinned in vendor.lock (clones if missing).
vendor:
	@./tools/vendor.sh

## Verify vendor/ checkouts match vendor.lock; fail on drift.
vendor-verify:
	@./tools/vendor.sh verify

## Run all local tests (Python harness only)
test: vendor-verify test-harness test-bootstrap test-demos

## Reproduce full CI locally using Docker.
## First run builds the Docker image; subsequent runs use layer cache.
test-ci: vendor test _docker-ensure
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c '$(PYTHON) -m pytest tests/ -v --tb=short'
	@echo "--- All CI checks passed ---"

# Internal: build the Docker image if it doesn't already exist.
_docker-ensure:
	@docker image inspect $(DOCKER_IMAGE) > /dev/null 2>&1 \
	    || (echo "Building $(DOCKER_IMAGE) Docker image (first time only)..." \
	        && docker build -t $(DOCKER_IMAGE) dev/docker/)

## Run all Python harness tests
test-harness: test-plan test-seed
	@echo "All harness tests passed."

## Run PLAN evaluator tests
test-plan:
	$(PYTHON) tests/sanity/test_plan.py

## Run seed round-trip tests
test-seed:
	$(PYTHON) tests/sanity/test_seed.py

## Run bootstrap compiler tests
test-bootstrap:
	$(PYTHON) tests/bootstrap/test_bootstrap.py
	$(PYTHON) tests/bootstrap/test_lexer.py
	$(PYTHON) tests/bootstrap/test_parser.py
	$(PYTHON) tests/bootstrap/test_scope.py
	$(PYTHON) tests/bootstrap/test_typecheck.py
	$(PYTHON) tests/bootstrap/test_codegen.py

## Run demo tests
test-demos:
	$(PYTHON) -m pytest tests/demos/ -v

## Render a demo as Glass IR on stdout.
## Usage: make demo-glass-ir ARGS=demos/csv_table.gls
##        make demo-glass-ir ARGS="demos/calculator.gls Calculator"
demo-glass-ir:
	@if [ -z "$(ARGS)" ]; then \
	    echo "Usage: make demo-glass-ir ARGS=demos/<name>.gls [Module.Name]" >&2; \
	    exit 1; \
	fi
	@$(PYTHON) -m bootstrap.render_demo $(ARGS)

## Build the Docker image (run once; takes a few minutes)
docker-build:
	docker build -t $(DOCKER_IMAGE) dev/docker/

## Run all compiler tests
test-compiler:
	$(PYTHON) -m pytest tests/compiler/ -v

## Run M8.8 self-hosting tests
test-selfhost:
	$(PYTHON) -m pytest tests/compiler/test_selfhost.py -v

## Run all compiler tests inside Docker (macOS-friendly)
## Build first: make docker-build
test-selfhost-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'python3 -m pytest tests/compiler/test_selfhost.py -v'

## Run the dev harness CLI
run:
	$(PYTHON) -m dev.harness.run $(ARGS)

## Remove generated files
clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

## Show available targets
help:
	@grep -E '^## ' Makefile | sed 's/^## //'
