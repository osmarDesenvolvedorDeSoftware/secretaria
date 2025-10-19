from flask import Flask

from app.services.abtest_service import ABTestService


def test_abtest_service_selects_best_variant(app: Flask) -> None:
    service = ABTestService(app.db_session, app.redis)  # type: ignore[attr-defined]
    with app.app_context():
        payload = {
            "company_id": 1,
            "template_base": "default",
            "epsilon": 0.0,
            "variant_a": {"template": "default"},
            "variant_b": {"template": "promocional"},
        }
        created = service.create_test(1, payload)
        service.start_test(1, created["id"])
        for _ in range(5):
            service.record_event(1, created["id"], "A", "impression")
            service.record_event(1, created["id"], "B", "impression")
        for _ in range(3):
            service.record_event(1, created["id"], "B", "conversion")
        selection = service.select_variant(1, "default")
        assert selection is not None
        assert selection.variant == "B"
        assert selection.template_name == "promocional"
        metrics = service.list_tests(1)[0]["metrics"]
        assert metrics["B"]["conversions"] == 3
