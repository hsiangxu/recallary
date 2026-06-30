from pathlib import Path

from recallary.config import Settings
from recallary.launchers import make_launcher


def test_make_windows_launcher(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    python_dir = tmp_path / "env" / "Scripts"
    python_dir.mkdir(parents=True)
    python = python_dir / "python.exe"
    pythonw = python_dir / "pythonw.exe"
    python.write_text("", encoding="utf-8")
    pythonw.write_text("", encoding="utf-8")

    result = make_launcher(
        settings,
        python_executable=python,
        platform_name="win32",
    )

    assert result.path == tmp_path / "Recallary.vbs"
    assert result.log_path == tmp_path / "data" / "logs" / "launcher-windows.log"
    assert result.python_path == pythonw
    content = result.path.read_text(encoding="utf-8")
    assert "pythonw.exe" in content
    assert "-m recallary.launcher" in content


def test_make_macos_launcher(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    python = tmp_path / "env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    result = make_launcher(
        settings,
        python_executable=python,
        platform_name="darwin",
    )

    app_path = tmp_path / "Recallary.app"
    script_path = app_path / "Contents" / "MacOS" / "Recallary"
    plist_path = app_path / "Contents" / "Info.plist"
    assert result.path == app_path
    assert result.log_path == tmp_path / "data" / "logs" / "launcher-macos.log"
    assert script_path.is_file()
    assert plist_path.is_file()
    script = script_path.read_text(encoding="utf-8")
    assert "-m recallary.launcher" in script
