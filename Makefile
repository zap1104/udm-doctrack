# Shortcuts. Run "make help" to see them all.
.DEFAULT_GOAL := help
PY := .venv/bin/python
PIP := .venv/bin/pip

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## First-time setup (venv, deps, .env, database, demo data)
	bash scripts/setup.sh

run: ## Start the development server
	$(PY) manage.py runserver

migrations: ## Create migration files after changing models
	$(PY) manage.py makemigrations accounts core tracking documents search

migrate: ## Apply migrations to the database
	$(PY) manage.py migrate

seed: ## Reload the demo data
	$(PY) manage.py seed_demo

reindex: ## Rebuild the search index
	$(PY) manage.py reindex_documents

test: ## Run the test suite
	$(PY) -m pytest

lint: ## Check code style
	.venv/bin/ruff check .

format: ## Fix code style automatically
	.venv/bin/ruff check . --fix
	.venv/bin/ruff format .

check: ## Django's own system check
	$(PY) manage.py check

worker: ## Start the background job worker (OCR, bulk indexing)
	$(PY) manage.py qcluster

shell: ## Open a Django shell
	$(PY) manage.py shell

export-training: ## Export reviewed metadata as AI training data
	$(PY) manage.py export_training_data --out training/metadata.jsonl

.PHONY: help setup run migrations migrate seed reindex test lint format check worker shell export-training
