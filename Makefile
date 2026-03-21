# Gallowglass — top-level Makefile

PYTHON := python3

.PHONY: test test-harness test-plan test-seed test-bootstrap clean help

## Run all local tests
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

## Run bootstrap compiler structural tests
test-bootstrap:
	$(PYTHON) tests/bootstrap/test_bootstrap.py
	$(PYTHON) tests/bootstrap/test_lexer.py
	$(PYTHON) tests/bootstrap/test_parser.py

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
