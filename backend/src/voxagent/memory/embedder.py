from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

EMBEDDING_DIMENSION = 512
MAX_TOKENS = 512


class Embedder(Protocol):
    def encode(self, texts: tuple[str, ...]) -> np.ndarray: ...


def l2_normalize_rows(values: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2:
        raise ValueError("embedding matrix must be two-dimensional")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("cannot normalize a zero-length embedding")
    return np.asarray(matrix / norms, dtype=np.float32)


class BgeSmallZhEmbedder:
    def __init__(self, tokenizer: object, session: object) -> None:
        self._tokenizer = tokenizer
        self._session = session

    @classmethod
    def from_path(cls, path: Path, threads: int = 4) -> BgeSmallZhEmbedder:
        if threads < 1:
            raise ValueError("threads must be positive")
        model_root = path.expanduser().resolve(strict=True)
        tokenizer_path = model_root / "tokenizer.json"
        model_path = model_root / "model_quantized.onnx"
        external_data_path = model_root / "model_quantized.onnx_data"
        for required in (tokenizer_path, model_path, external_data_path):
            if not required.is_file():
                raise FileNotFoundError(required)

        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        return cls(tokenizer, session)

    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        if not texts:
            return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [item.attention_mask for item in encodings], dtype=np.int64
        )
        token_type_ids = np.asarray([item.type_ids for item in encodings], dtype=np.int64)
        available_inputs = {item.name for item in self._session.get_inputs()}
        candidates = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        feed = {name: value for name, value in candidates.items() if name in available_inputs}
        hidden_state = np.asarray(self._session.run(None, feed)[0], dtype=np.float32)
        if hidden_state.ndim != 3 or hidden_state.shape[2] != EMBEDDING_DIMENSION:
            raise ValueError("BGE model output must contain 512-dimensional token vectors")
        cls_vectors = hidden_state[:, 0, :]
        return l2_normalize_rows(cls_vectors)
