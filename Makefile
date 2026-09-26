PY := .venv/bin/python

.PHONY: setup test test-pg pg-down

setup:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

test:
	$(PY) -m pytest -q

# Starts Postgres 16 (docker-compose.yml), waits for its healthcheck, runs the postgres-marked tests.
# The container is left running; `make pg-down` stops it.
test-pg:
	docker compose up -d --wait postgres
	$(PY) -m pytest -q -m postgres

pg-down:
	docker compose down
