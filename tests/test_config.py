import os
from pathlib import Path

from recallary.config import Settings


def test_runtime_storage_stays_inside_project(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    settings.configure_local_storage()

    for variable in (
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_ASSETS_CACHE",
        "HF_XET_CACHE",
        "HF_TOKEN_PATH",
        "TRANSFORMERS_CACHE",
        "SENTENCE_TRANSFORMERS_HOME",
        "TORCH_HOME",
        "TORCH_EXTENSIONS_DIR",
        "XDG_CACHE_HOME",
        "MPLCONFIGDIR",
        "NUMBA_CACHE_DIR",
        "TMPDIR",
        "TEMP",
        "TMP",
    ):
        value = Path(os.environ[variable]).resolve()
        assert value.is_relative_to(tmp_path.resolve())
