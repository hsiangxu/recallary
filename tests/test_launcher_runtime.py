from pathlib import Path

from filelock import FileLock

from recallary.config import Settings
from recallary import launcher


def test_launcher_exits_when_app_lock_is_held(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(root=tmp_path)
    settings.configure_local_storage()
    lock = FileLock(settings.app_lock_path, timeout=0)
    lock.acquire()
    notifications: list[tuple[str, str]] = []
    try:
        monkeypatch.setenv("RECALLARY_ROOT", str(tmp_path))
        monkeypatch.setattr(
            launcher,
            "_notify_user",
            lambda title, message: notifications.append((title, message)),
        )

        assert launcher.main() == 0
    finally:
        lock.release()

    assert notifications
    assert "already running" in notifications[0][1]
    assert "already running" in launcher._log_path(settings).read_text(
        encoding="utf-8"
    )
