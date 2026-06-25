from __future__ import annotations

from recallary.config import Settings


def main() -> None:
    settings = Settings.from_root()
    settings.configure_local_storage()

    from recallary.cli import app

    app()

