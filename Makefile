.PHONY: dev migrate upgrade worker test up down install revision ci-migrate report

export FLASK_APP=run.py

FORMAT ?= csv

install:
        python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

dev:
        alembic upgrade head
        flask --app run.py --debug run --host=0.0.0.0 --port=8080

migrate:
        alembic upgrade head

revision:
        alembic revision --autogenerate -m "manual"

upgrade:
        alembic upgrade head

worker:
	alembic upgrade head
	python -m app.workers.rq_worker

up:
	docker compose -f docker/docker-compose.yml up -d --build

down:
	docker compose -f docker/docker-compose.yml down -v

test:
        pytest -v --maxfail=1 --disable-warnings --cov=app --cov-report=term-missing

ci-migrate:
        alembic upgrade head || alembic downgrade -1

report:
	@if [ -z "$(COMPANY_ID)" ]; then \
		echo "Uso: make report COMPANY_ID=<id> [FORMAT=csv|pdf]"; \
		exit 1; \
	fi
	python -m scripts.export_report --company-id $(COMPANY_ID) --format $(FORMAT)
