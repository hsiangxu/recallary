from __future__ import annotations

import sys

from recallary.config import Settings


def main() -> None:
    settings = Settings.from_root()
    settings.configure_local_storage()

    if len(sys.argv) == 1 or (len(sys.argv) >= 2 and sys.argv[1] == "gui"):
        if len(sys.argv) >= 2 and sys.argv[1] == "gui":
            sys.argv.pop(1)
        try:
            from recallary.gui.app import main as gui_main
        except ImportError as error:
            raise RuntimeError(
                "The GUI dependency is missing. Install the Conda environment "
                "from environment.yml, then run Recallary again."
            ) from error
        raise SystemExit(gui_main())

    cli_main()


def cli_main() -> None:
    settings = Settings.from_root()
    settings.configure_local_storage()

    from recallary.cli import app

    app()
