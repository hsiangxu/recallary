from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


MODEL_ID = "intfloat/multilingual-e5-small"
INDEX_VERSION = 1
DEFAULT_LIMIT = 10
DEFAULT_BATCH_SIZE = 8


def find_project_root(start: Path | None = None) -> Path:
    configured = os.environ.get("RECALLARY_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "recallary"
        ).is_dir():
            return candidate

    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "pyproject.toml").is_file():
        return package_root
    raise RuntimeError(
        "Could not locate the Recallary project root. Run the command from the "
        "repository or set RECALLARY_ROOT."
    )


@dataclass(frozen=True)
class Settings:
    root: Path
    model_id: str = MODEL_ID
    batch_size: int = DEFAULT_BATCH_SIZE

    @classmethod
    def from_root(cls, root: Path | None = None) -> "Settings":
        return cls(root=(root or find_project_root()).resolve())

    @property
    def library_dir(self) -> Path:
        return self.root / "library"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "recallary.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def model_dir(self) -> Path:
        return self.models_dir / self.model_id.replace("/", "--")

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def temp_dir(self) -> Path:
        return self.runtime_dir / "tmp"

    @property
    def index_lock_path(self) -> Path:
        return self.runtime_dir / "index.lock"

    def ensure_directories(self) -> None:
        for directory in (
            self.library_dir,
            self.data_dir,
            self.models_dir,
            self.cache_dir,
            self.runtime_dir,
            self.logs_dir,
            self.temp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def configure_local_storage(self) -> None:
        """Keep caches and temporary files created by Recallary inside the repo."""
        self.ensure_directories()
        paths = {
            "HF_HOME": self.cache_dir / "huggingface",
            "HF_HUB_CACHE": self.cache_dir / "huggingface" / "hub",
            "HUGGINGFACE_HUB_CACHE": self.cache_dir / "huggingface" / "hub",
            "HF_ASSETS_CACHE": self.cache_dir / "huggingface" / "assets",
            "HF_XET_CACHE": self.cache_dir / "huggingface" / "xet",
            "HF_TOKEN_PATH": self.cache_dir / "huggingface" / "token",
            "TRANSFORMERS_CACHE": self.cache_dir / "huggingface" / "transformers",
            "SENTENCE_TRANSFORMERS_HOME": self.cache_dir
            / "sentence-transformers",
            "TORCH_HOME": self.cache_dir / "torch",
            "TORCH_EXTENSIONS_DIR": self.cache_dir / "torch-extensions",
            "XDG_CACHE_HOME": self.cache_dir,
            "MPLCONFIGDIR": self.cache_dir / "matplotlib",
            "NUMBA_CACHE_DIR": self.cache_dir / "numba",
            "TMPDIR": self.temp_dir,
            "TEMP": self.temp_dir,
            "TMP": self.temp_dir,
        }
        for name, path in paths.items():
            directory = path.parent if name == "HF_TOKEN_PATH" else path
            directory.mkdir(parents=True, exist_ok=True)
            os.environ[name] = str(path)

        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("DO_NOT_TRACK", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
