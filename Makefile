N10S_VERSION ?= 5.26.0
# Prefer the project venv on either platform; fall back to whatever is on PATH.
VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/Scripts/python))
PYTHON ?= $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)

# Defaults matching docker-compose.yml. Override in the environment if your
# stack uses different credentials.
export BEOMCP_NEO4J_USER ?= neo4j
export BEOMCP_NEO4J_PASSWORD ?= password
export BEOMCP_NEO4J_URI ?= bolt://localhost:7687

.PHONY: demo test unit integration e2e lint-projection projection seed up down

up:
	docker compose up -d --wait

down:
	docker compose down -v

projection:
	$(PYTHON) -m beomcp.cli project

seed: projection
	$(PYTHON) -m beomcp.cli seed --abox ontology/abox.ttl

lint-projection: projection
	git diff --exit-code -- ontology/schema.projection.json || \
		(echo "schema.projection.json out of date; commit the regenerated file" && exit 1)

unit:
	$(PYTHON) -m pytest tests/unit -v

integration:
	$(PYTHON) -m pytest tests/integration -v

e2e: projection
	$(PYTHON) -m pytest tests/e2e -v

test: unit integration

demo: up seed
	$(PYTHON) -m pytest tests/e2e -v
