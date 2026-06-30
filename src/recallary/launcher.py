from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from recallary.config import Settings


READY_TIMEOUT_SECONDS = 10.0
READY_POLL_SECONDS = 0.2


def _log_path(settings: Settings) -> Path:
    if sys.platform.startswith("win"):
        filename = "launcher-windows.log"
    elif sys.platform == "darwin":
        filename = "launcher-macos.log"
    else:
        filename = "launcher.log"
    return settings.logs_dir / filename


def _notify_user(title: str, message: str) -> None:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
            return
        if sys.platform == "darwin":
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display dialog "{safe_message}" '
                    f'with title "{safe_title}" buttons {{"OK"}} default button "OK"',
                ],
                check=False,
            )
    except Exception:
        return


def _pythonw_path() -> Path:
    python = Path(sys.executable)
    if sys.platform.startswith("win"):
        pythonw = python.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return python


def _ready_token(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("token="):
                return line.removeprefix("token=").strip()
    except OSError:
        return None
    return None


def _terminate(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _start_gui_child(settings: Settings, token: str) -> subprocess.Popen[object]:
    command = [
        os.fspath(_pythonw_path()),
        "-m",
        "recallary.gui_runner",
        "--ready-token",
        token,
    ]
    return subprocess.Popen(
        command,
        cwd=os.fspath(settings.root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _wait_for_ready(
    settings: Settings,
    process: subprocess.Popen[object],
    token: str,
) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _ready_token(settings.gui_ready_path) == token:
            try:
                settings.gui_ready_path.unlink()
            except OSError:
                pass
            return True
        time.sleep(READY_POLL_SECONDS)
    return False


def main() -> int:
    settings = Settings.from_root()
    settings.configure_local_storage()
    log_path = _log_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            print()
            print(
                f"[{datetime.now(UTC).isoformat(timespec='seconds')}] "
                "Starting Recallary launcher",
                flush=True,
            )
            print(f"Python: {sys.executable}", flush=True)
            print(f"Project: {settings.root}", flush=True)
            try:
                try:
                    probe_lock = FileLock(settings.app_lock_path, timeout=0)
                    probe_lock.acquire()
                    probe_lock.release()
                except Timeout:
                    message = (
                        "Recallary is already running.\n\n"
                        f"If you do not see the window, check:\n{log_path}"
                    )
                    print(message, flush=True)
                    _notify_user("Recallary", message)
                    return 0

                try:
                    settings.gui_ready_path.unlink()
                except OSError:
                    pass

                token = uuid.uuid4().hex
                process = _start_gui_child(settings, token)
                print(f"Started GUI child PID: {process.pid}", flush=True)
                if _wait_for_ready(settings, process, token):
                    print("GUI reported ready.", flush=True)
                    return 0

                exit_code = process.poll()
                if exit_code is None:
                    print(
                        "GUI did not report ready before timeout; terminating child.",
                        flush=True,
                    )
                    _terminate(process)
                    detail = "The GUI did not become ready before the startup timeout."
                else:
                    detail = f"The GUI process exited early with code {exit_code}."
                    print(detail, flush=True)

                message = (
                    "Recallary failed to start.\n\n"
                    f"{detail}\n\n"
                    f"See the launcher log:\n{log_path}"
                )
                _notify_user("Recallary failed to start", message)
                return 1
            except Exception:
                traceback.print_exc()
                message = (
                    "Recallary failed to start.\n\n"
                    f"See the launcher log:\n{log_path}"
                )
                print(message, flush=True)
                _notify_user("Recallary failed to start", message)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
