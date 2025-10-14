from __future__ import annotations

import redis
from rq import Connection, Worker

from app.config import settings


if __name__ == "__main__":
    redis_conn = redis.from_url(settings.redis_url)
    with Connection(redis_conn):
        worker = Worker([settings.queue_name])
        worker.work()
