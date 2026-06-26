from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from recallary.config import Settings


class ModelNotInstalledError(RuntimeError):
    pass


MODEL_READY_MARKER = ".recallary-model-ready"


def model_is_installed(model_dir: Path) -> bool:
    return (model_dir / "config.json").is_file() and (
        model_dir / MODEL_READY_MARKER
    ).is_file()


def download_model(settings: Settings) -> Path:
    settings.configure_local_storage()
    if model_is_installed(settings.model_dir):
        return settings.model_dir

    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=settings.model_id,
        local_dir=settings.model_dir,
        cache_dir=settings.cache_dir / "huggingface" / "hub",
        ignore_patterns=[
            ".eval_results/*",
            "onnx/*",
            "openvino/*",
            "pytorch_model.bin",
        ],
    )
    if not (settings.model_dir / "config.json").is_file():
        raise RuntimeError("Model download completed without a model config.")
    (settings.model_dir / MODEL_READY_MARKER).write_text(
        f"{settings.model_id}\n",
        encoding="utf-8",
    )
    return settings.model_dir


class Embedder:
    def __init__(self, settings: Settings):
        settings.configure_local_storage()
        if not model_is_installed(settings.model_dir):
            raise ModelNotInstalledError(
                "The embedding model is not installed. Run `recallary setup`."
            )
        from sentence_transformers import SentenceTransformer

        self.batch_size = settings.batch_size
        self.model = SentenceTransformer(
            str(settings.model_dir),
            device="cpu",
        )

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        prefixed = [f"passage: {text}" for text in texts]
        return np.asarray(
            self.model.encode(
                prefixed,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            ),
            dtype=np.float32,
        )

    def encode_query(self, query: str) -> np.ndarray:
        vector = self.model.encode(
            [f"query: {query.strip()}"],
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vector[0], dtype=np.float32)
