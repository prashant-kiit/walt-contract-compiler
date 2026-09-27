PY := .venv/bin/python

.PHONY: setup run test test-pg pg-down

setup:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

# Demo: compile Contract A for DuckDB, run it on the PDF seed and print the table.
run:
	$(PY) -m walt_compiler run --model fixtures/pdf/semantic_model.json --contract fixtures/pdf/contracts/a.json

test:
	$(PY) -m pytest -q

# Starts Postgres 16 (docker-compose.yml), waits for its healthcheck, runs the postgres-marked tests.
# The container is left running; `make pg-down` stops it.
test-pg:
	docker compose up -d --wait postgres
	$(PY) -m pytest -q -m postgres

pg-down:
	docker compose down
