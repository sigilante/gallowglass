# Gallowglass — top-level Makefile

PYTHON := python3
PLANVM ?= planvm
DOCKER_IMAGE := gallowglass-dev

.PHONY: test test-harness test-plan test-seed test-bootstrap \
        test-planvm test-planvm-docker docker-build clean help

## Run all local tests (Python harness only — no planvm required)
test: test-harness test-bootstrap

## Run all Python harness tests
test-harness: test-plan test-seed
	@echo "All harness tests passed."

## Run PLAN evaluator tests
test-plan:
	$(PYTHON) tests/sanity/test_plan.py

## Run seed round-trip tests
test-seed:
	$(PYTHON) tests/sanity/test_seed.py

## Run bootstrap compiler tests (no planvm required)
test-bootstrap:
	$(PYTHON) tests/bootstrap/test_bootstrap.py
	$(PYTHON) tests/bootstrap/test_lexer.py
	$(PYTHON) tests/bootstrap/test_parser.py
	$(PYTHON) tests/bootstrap/test_scope.py
	$(PYTHON) tests/bootstrap/test_typecheck.py
	$(PYTHON) tests/bootstrap/test_codegen.py

## Validate compiled seeds against x/plan (requires planvm on PATH or PLANVM=...)
## On macOS, use `make test-planvm-docker` instead.
test-planvm:
	PLANVM=$(PLANVM) $(PYTHON) tests/planvm/test_seed_planvm.py

## Build the Docker image containing planvm (run once; takes a few minutes)
docker-build:
	docker build -t $(DOCKER_IMAGE) dev/docker/

## Run planvm seed validation inside Docker (macOS-friendly, requires Docker Desktop)
## Build first: make docker-build
test-planvm-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) tests/planvm/test_seed_planvm.py'

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
