from __future__ import annotations

import argparse

import redis
from rq import Connection, Worker

from app import init_app
from app.config import settings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a Secretaria RQ worker")
    parser.add_argument(
        "--queue",
        "-q",
        action="append",
        dest="queues",
        help="Fila(s) que o worker deve processar",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Processa jobs disponíveis e encerra",
    )
    args = parser.parse_args()

    queue_names = args.queues if args.queues else [settings.queue_name]

    redis_conn = redis.from_url(settings.redis_url)
    app = init_app()

    with app.app_context():
        with Connection(redis_conn):
            worker = Worker(queue_names)
            worker.work(burst=args.burst)
