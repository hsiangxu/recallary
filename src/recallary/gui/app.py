from __future__ import annotations

import uuid

from recallary.gui_runner import main as gui_runner_main


def main() -> int:
    return gui_runner_main(["--ready-token", uuid.uuid4().hex])
