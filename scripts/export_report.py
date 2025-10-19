from __future__ import annotations

import argparse
from pathlib import Path

from app import init_app
from app.services.analytics_service import AnalyticsService


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta relatórios de analytics por empresa.")
    parser.add_argument("--company-id", type=int, required=True, help="ID da empresa alvo")
    parser.add_argument(
        "--format",
        dest="format",
        choices=("csv", "pdf"),
        default="csv",
        help="Formato do relatório (csv ou pdf)",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default="reports",
        help="Diretório onde o arquivo será salvo",
    )
    args = parser.parse_args()

    app = init_app()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        analytics_service: AnalyticsService | None = getattr(app, "analytics_service", None)
        if analytics_service is None:
            analytics_service = AnalyticsService(app.db_session, app.redis)  # type: ignore[attr-defined]
        filename, _content_type, payload = analytics_service.export_report(args.company_id, args.format)

    output_path = output_dir / filename
    output_path.write_bytes(payload)
    print(f"Relatório gerado em {output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
