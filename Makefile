.PHONY: dev migrate upgrade worker test up down install

export FLASK_APP=run.py

install:
	python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

dev:
	flask --app run.py --debug run --host=0.0.0.0 --port=8080

migrate:
	alembic revision --autogenerate -m "manual"

upgrade:
	alembic upgrade head

worker:
	python -m app.workers.rq_worker

up:
	docker compose -f docker/docker-compose.yml up -d --build

down:
	docker compose -f docker/docker-compose.yml down -v

test:
	pytest -v --maxfail=1 --disable-warnings --cov=app --cov-report=term-missing
