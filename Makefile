.PHONY: build check format-check lint schema-check schemas test type

check: format-check lint type schema-check test build

format-check:
	uv lock --check
	uv run --locked ruff format --check .

lint:
	uv run --locked ruff check .

type:
	uv run --locked mypy src

schemas:
	uv run --locked python scripts/generate_schemas.py

schema-check:
	uv run --locked python scripts/generate_schemas.py --check

test:
	uv run --locked pytest --cov=tracefetch --cov-report=term --cov-fail-under=80

build:
	uv build
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git diff --check -- .; fi
