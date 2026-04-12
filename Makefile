.PHONY: install dev test lint format run

install:
	pip install -e .[dev]

dev:
	uvicorn app.api.main:app --reload

test:
	pytest -q

lint:
	ruff check .
	mypy app

run:
	python -m app.api.main
