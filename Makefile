PYTHON ?= python3

.PHONY: test test-unit lint typecheck

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m unittest discover -s tests

lint:
	$(PYTHON) -m ruff check app tests

typecheck:
	$(PYTHON) -m mypy app
