# Gallowglass — top-level Makefile

PYTHON := python3
PLANVM ?= planvm
DOCKER_IMAGE := gallowglass-dev

.PHONY: test test-ci test-harness test-plan test-seed test-bootstrap \
        test-planvm test-planvm-docker test-prelude test-prelude-docker \
        test-compiler test-selfhost test-selfhost-docker \
        test-demos \
        docker-build _docker-ensure clean help

## Run all local tests (Python harness only — no planvm required)
test: test-harness test-bootstrap test-demos

## Reproduce full CI locally using Docker (= make test + planvm seed validation).
## First run builds the Docker image (~5 min); subsequent runs use layer cache.
## Use this as the gate before opening a PR.
test-ci: test _docker-ensure
	@echo "--- planvm seed validation (Docker) ---"
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) tests/planvm/test_seed_planvm.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_combinators.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_bool.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_nat.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_option.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_list.py'
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

## Run bootstrap compiler tests (no planvm required)
test-bootstrap:
	$(PYTHON) tests/bootstrap/test_bootstrap.py
	$(PYTHON) tests/bootstrap/test_lexer.py
	$(PYTHON) tests/bootstrap/test_parser.py
	$(PYTHON) tests/bootstrap/test_scope.py
	$(PYTHON) tests/bootstrap/test_typecheck.py
	$(PYTHON) tests/bootstrap/test_codegen.py

## Run demo tests (no planvm required)
test-demos:
	$(PYTHON) -m pytest tests/demos/ -v

## Validate compiled seeds against x/plan (requires planvm on PATH or PLANVM=...)
## On macOS, use `make test-planvm-docker` instead.
test-planvm:
	PLANVM=$(PLANVM) $(PYTHON) tests/planvm/test_seed_planvm.py

## Build the Docker image containing planvm (run once; takes a few minutes)
docker-build:
	docker build -t $(DOCKER_IMAGE) dev/docker/

## Run all compiler tests (harness-level, no planvm required)
test-compiler:
	$(PYTHON) -m pytest tests/compiler/ -v

## Run M8.8 self-hosting tests (harness-level, no planvm required)
test-selfhost:
	$(PYTHON) -m pytest tests/compiler/test_selfhost.py -v

## Run M8.8 self-hosting tests inside Docker with planvm active (macOS-friendly)
## Build first: make docker-build
test-selfhost-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm python3 -m pytest tests/compiler/test_selfhost.py -v'

## Run all compiler tests inside Docker with planvm active (macOS-friendly)
## Build first: make docker-build
test-compiler-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm python3 -m pytest tests/compiler/ -v'

## Run planvm seed validation inside Docker (macOS-friendly, requires Docker Desktop)
## Build first: make docker-build
test-planvm-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) tests/planvm/test_seed_planvm.py'

## Validate Core prelude seeds against x/plan (requires planvm on PATH or PLANVM=...)
## On macOS, use `make test-prelude-docker` instead.
test-prelude:
	PLANVM=$(PLANVM) $(PYTHON) tests/prelude/test_core_combinators.py
	PLANVM=$(PLANVM) $(PYTHON) tests/prelude/test_core_bool.py
	PLANVM=$(PLANVM) $(PYTHON) tests/prelude/test_core_nat.py
	PLANVM=$(PLANVM) $(PYTHON) tests/prelude/test_core_option.py
	PLANVM=$(PLANVM) $(PYTHON) tests/prelude/test_core_list.py

## Run Core prelude seed validation inside Docker (macOS-friendly)
test-prelude-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) tests/prelude/test_core_combinators.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_bool.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_nat.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_option.py && \
	           PLANVM=planvm $(PYTHON) tests/prelude/test_core_list.py'

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
