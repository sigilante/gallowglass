# Gallowglass — top-level Makefile

PYTHON := python3
PLANVM ?= planvm
DOCKER_IMAGE := gallowglass-dev

.PHONY: test test-ci test-harness test-plan test-seed test-bootstrap \
        test-planvm test-planvm-docker test-prelude test-prelude-docker \
        test-compiler test-selfhost test-selfhost-docker \
        test-eval test-eval-docker \
        test-demos demo-glass-ir \
        vendor vendor-verify \
        docker-build _docker-ensure clean help

## Populate vendor/ at SHAs pinned in vendor.lock (clones if missing).
vendor:
	@./tools/vendor.sh

## Verify vendor/ checkouts match vendor.lock; fail on drift.
vendor-verify:
	@./tools/vendor.sh verify

## Run all local tests (Python harness only — no planvm required)
test: test-harness test-bootstrap test-demos

## Reproduce full CI locally using Docker (= make test + planvm validation).
## First run builds the Docker image (~5 min); subsequent runs use layer cache.
## Use this as the gate before opening a PR.
test-ci: test _docker-ensure
	@echo "--- planvm seed + eval validation (Docker) ---"
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) -m pytest tests/ -v --tb=short'
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

## Render a demo as Glass IR on stdout.
## Usage: make demo-glass-ir ARGS=demos/csv_table.gls
##        make demo-glass-ir ARGS="demos/calculator.gls Calculator"
demo-glass-ir:
	@if [ -z "$(ARGS)" ]; then \
	    echo "Usage: make demo-glass-ir ARGS=demos/<name>.gls [Module.Name]" >&2; \
	    exit 1; \
	fi
	@$(PYTHON) -m bootstrap.render_demo $(ARGS)

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
	    sh -c 'PLANVM=planvm $(PYTHON) -m pytest tests/planvm/ -v --tb=short'

## Validate compiled programs evaluate correctly on planvm (requires planvm)
test-eval:
	PLANVM=$(PLANVM) $(PYTHON) -m pytest tests/planvm/test_eval_planvm.py -v

## Run evaluation tests inside Docker (macOS-friendly)
test-eval-docker:
	docker run --rm -v "$(PWD):/work" $(DOCKER_IMAGE) \
	    sh -c 'PLANVM=planvm $(PYTHON) -m pytest tests/planvm/test_eval_planvm.py -v --tb=short'

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
	    sh -c 'PLANVM=planvm $(PYTHON) -m pytest tests/prelude/ -v --tb=short'

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
