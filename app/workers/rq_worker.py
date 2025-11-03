from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import List, Sequence

import redis
import structlog
from rq import Connection, Worker

from app import init_app
from app.config import settings
from app.services.tenancy import iter_companies, queue_name_for_company


LOGGER = structlog.get_logger(__name__).bind(component="rq_worker")


def _ensure_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _queues_for_companies(company_ids: Sequence[int]) -> list[str]:
    queues: list[str] = []
    for company_id in company_ids:
        queues.append(queue_name_for_company(settings.queue_name, company_id))
        queues.append(queue_name_for_company(settings.dead_letter_queue_name, company_id))
    return queues


def _discover_company_ids(app) -> list[int]:
    try:
        companies = iter_companies(app.db_session)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - defensive safeguard
        LOGGER.warning("company_discovery_failed", error=str(exc))
        return []
    return [company.id for company in companies]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a Secretaria RQ worker")
    parser.add_argument(
        "--queue",
        "-q",
        action="append",
        dest="queues",
        help="Fila(s) completas que o worker deve processar (pode ser usado várias vezes)",
    )
    parser.add_argument(
        "--company-id",
        action="append",
        type=int,
        dest="company_ids",
        help="Processa filas isoladas da empresa informada (pode ser usado várias vezes)",
    )
    parser.add_argument(
        "--all-tenants",
        action="store_true",
        help="Descobre automaticamente todas as empresas cadastradas e processa suas filas",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Processa jobs disponíveis e encerra",
    )
    args = parser.parse_args()

    app = init_app()

    requested_company_ids = args.company_ids or []
    if args.all_tenants:
        requested_company_ids.extend(_discover_company_ids(app))

    queue_names: List[str] = []
    queue_names.extend(args.queues or [])
    if requested_company_ids:
        queue_names.extend(_queues_for_companies(requested_company_ids))

    # Garantir que filas padrão (company 0) sempre sejam monitoradas
    queue_names.extend(
        _queues_for_companies([0])
    )

    if not queue_names:
        # Fallback para compatibilidade retroativa
        queue_names = [settings.queue_name, settings.dead_letter_queue_name]

    queue_names = _ensure_unique(queue_names)

    LOGGER.info("worker_starting", queues=queue_names)

    redis_conn = redis.from_url(settings.redis_url)

    with app.app_context():
        with Connection(redis_conn):
            worker = Worker(queue_names)
            worker.work(burst=args.burst)
