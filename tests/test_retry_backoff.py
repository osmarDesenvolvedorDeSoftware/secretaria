from __future__ import annotations

import pytest

from app.config import settings
from app.models import DeliveryLog
from app.services.tasks import TaskService, process_incoming_message
from app.services.whaticket import WhaticketError


class DummyJob:
    def __init__(self) -> None:
        self.id = "job-1"
        self.meta: dict[str, int] = {}
        self._retries_left = 5

    @property
    def retries_left(self) -> int:
        return self._retries_left

    @retries_left.setter
    def retries_left(self, value: int) -> None:
        self._retries_left = value

    def save_meta(self) -> None:
        # In real RQ this persists the metadata. For tests this is a no-op.
        return None


def _clear_logs(app):
    session = app.db_session()  # type: ignore[attr-defined]
    session.query(DeliveryLog).delete()
    session.commit()
    session.close()


def test_enqueue_uses_progressive_retry(app, monkeypatch):
    service = TaskService(app.redis, app.db_session, app.task_queue)  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "rq_retry_delays", (5, 15, 45, 90))
    monkeypatch.setattr(settings, "rq_retry_max_attempts", 5)

    service.enqueue("5511999999999", "Oi", "text", "corr")
    job = app.task_queue.enqueued[0]
    retry = job[2]["retry"]
    assert retry.max == 5
    assert retry.intervals == [5, 15, 45, 90]


def test_retryable_failure_marks_status_and_metrics(app, monkeypatch):
    monkeypatch.setattr(settings, "rq_retry_delays", (5, 15, 45, 90))
    monkeypatch.setattr(settings, "rq_retry_max_attempts", 5)

    dummy_job = DummyJob()
    retries_sequence = [5, 4, 0]
    call_index = {"value": 0}

    def fake_get_current_job():
        idx = min(call_index["value"], len(retries_sequence) - 1)
        dummy_job.retries_left = retries_sequence[idx]
        call_index["value"] += 1
        return dummy_job

    monkeypatch.setattr("app.services.tasks.get_current_job", fake_get_current_job)

    monkeypatch.setattr(
        "app.services.tasks.LLMClient.generate_reply",
        lambda self, text, context: "Tudo certo",
    )

    monkeypatch.setattr(
        "app.services.tasks.WhaticketClient.send_text",
        lambda self, number, body: (_ for _ in ()).throw(WhaticketError("temp", retryable=True)),
    )

    from app.metrics import whaticket_send_retry_total

    baseline = whaticket_send_retry_total._value.get()

    with app.app_context():
        _clear_logs(app)
        with pytest.raises(WhaticketError):
            process_incoming_message("5511000000000", "Oi", "text", "c1")
        session = app.db_session()  # type: ignore[attr-defined]
        logs = session.query(DeliveryLog).all()
        assert logs[-1].status == "FAILED_TEMPORARY"
        session.close()

        with pytest.raises(WhaticketError):
            process_incoming_message("5511000000000", "Oi", "text", "c1")
        session = app.db_session()  # type: ignore[attr-defined]
        logs = session.query(DeliveryLog).all()
        assert logs[-1].status == "FAILED_TEMPORARY"
        session.close()

        with pytest.raises(WhaticketError):
            process_incoming_message("5511000000000", "Oi", "text", "c1")
        session = app.db_session()  # type: ignore[attr-defined]
        logs = session.query(DeliveryLog).all()
        assert logs[-1].status == "FAILED_PERMANENT"
        session.close()

    assert whaticket_send_retry_total._value.get() >= baseline + 3
