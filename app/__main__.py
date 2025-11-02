from __future__ import annotations

import os

from . import init_app


def main() -> None:
    app = init_app()
    port = int(os.getenv("PORT", "5005"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
