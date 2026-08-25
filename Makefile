.PHONY: help venv install crosswalk official estimate load all db-init test clean

help:
	@echo "make install     create .venv and install the package (editable) + dev deps"
	@echo "make db-init     create the nhbot database and apply the schema (no-postgis)"
	@echo "make all         run the full pipeline: crosswalk -> official -> estimate -> load"
	@echo "make test        run pytest"

venv:
	python3 -m venv .venv

install: venv
	. .venv/bin/activate && pip install -U pip && pip install -e '.[dev]'

db-init:
	createdb nhbot || true
	psql -d nhbot -f src/nhbot/db/schema_no_postgis.sql

crosswalk: ; . .venv/bin/activate && nhbot crosswalk
official:  ; . .venv/bin/activate && nhbot dra-official
estimate:  ; . .venv/bin/activate && nhbot dra-estimate
load:      ; . .venv/bin/activate && nhbot load
all:       ; . .venv/bin/activate && nhbot all

test: ; . .venv/bin/activate && pytest -q

clean: ; rm -rf .venv build dist src/*.egg-info .pytest_cache
