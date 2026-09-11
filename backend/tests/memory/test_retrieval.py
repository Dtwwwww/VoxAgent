from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from voxagent.memory.embedder import BgeSmallZhEmbedder, l2_normalize_rows
from voxagent.memory.embedding_manifest import EMBEDDING_MODEL
from voxagent.memory.retrieval import SqliteVectorRetriever, cosine_top_k


def test_l2_normalization_produces_unit_float32_rows() -> None:
    values = np.asarray([[3, 4], [0, 2]], dtype=np.float32)

    normalized = l2_normalize_rows(values)

    np.testing.assert_allclose(np.linalg.norm(normalized, axis=1), [1, 1])
    assert normalized.dtype == np.float32


def test_l2_normalization_rejects_zero_vectors() -> None:
    with pytest.raises(ValueError, match="zero-length embedding"):
        l2_normalize_rows(np.asarray([[0, 0]], dtype=np.float32))


def test_bge_embedder_uses_attention_masked_mean_pooling() -> None:
    class Encoding:
        ids = [1, 2, 0]
        attention_mask = [1, 1, 0]
        type_ids = [0, 0, 0]

    class Tokenizer:
        def encode_batch(self, _texts: list[str]) -> list[Encoding]:
            return [Encoding()]

    class Input:
        def __init__(self, name: str) -> None:
            self.name = name

    class Session:
        def get_inputs(self) -> list[Input]:
            return [Input("input_ids"), Input("attention_mask"), Input("token_type_ids")]

        def run(self, _outputs: object, _feed: dict[str, np.ndarray]) -> list[np.ndarray]:
            hidden = np.zeros((1, 3, 512), dtype=np.float32)
            hidden[0, 0, 0] = 1
            hidden[0, 1, 1] = 1
            hidden[0, 2, 2] = 100  # Padding must not affect the pooled vector.
            return [hidden]

    vector = BgeSmallZhEmbedder(Tokenizer(), Session()).encode(("测试",))[0]

    assert vector[0] == pytest.approx(2**-0.5)
    assert vector[1] == pytest.approx(2**-0.5)
    assert vector[2] == 0


def test_embedding_manifest_pins_required_files_and_sha256() -> None:
    assert EMBEDDING_MODEL.name == "bge-small-zh-v1.5"
    assert EMBEDDING_MODEL.dimension == 512
    assert {item.name for item in EMBEDDING_MODEL.files} == {
        "tokenizer.json",
        "model_quantized.onnx",
        "model_quantized.onnx_data",
    }
    assert all(len(item.sha256) == 64 for item in EMBEDDING_MODEL.files)
    assert all(item.size_bytes > 0 for item in EMBEDDING_MODEL.files)


def test_cosine_top_k_orders_descending_and_filters_minimum_score() -> None:
    query = np.asarray([1, 0], dtype=np.float32)
    matrix = np.asarray([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32)

    hits = cosine_top_k(query, matrix, ids=(10, 20, 30), k=5, minimum_score=0.75)

    assert [hit.id for hit in hits] == [10, 20]
    assert hits[0].score == pytest.approx(1)
    assert hits[1].score == pytest.approx(0.8)


def test_cosine_top_k_uses_id_as_deterministic_tie_breaker() -> None:
    query = np.asarray([1, 0], dtype=np.float32)
    matrix = np.asarray([[1, 0], [1, 0], [1, 0]], dtype=np.float32)

    hits = cosine_top_k(query, matrix, ids=(9, 3, 5), k=2, minimum_score=0)

    assert [hit.id for hit in hits] == [3, 5]


def test_cosine_top_k_handles_empty_corpus_and_clamps_k() -> None:
    query = np.asarray([1, 0], dtype=np.float32)
    assert cosine_top_k(query, np.empty((0, 2), dtype=np.float32), (), 3, 0) == ()

    hits = cosine_top_k(
        query,
        np.asarray([[1, 0]], dtype=np.float32),
        ids=(7,),
        k=10,
        minimum_score=0,
    )
    assert [hit.id for hit in hits] == [7]


@pytest.mark.parametrize(
    ("query", "matrix", "ids", "message"),
    [
        (np.asarray([1, 0]), np.ones((2, 3)), (1, 2), "dimension"),
        (np.asarray([1, 0]), np.ones((2, 2)), (1,), "IDs"),
        (np.asarray([[1, 0]]), np.ones((1, 2)), (1,), "one-dimensional"),
    ],
)
def test_cosine_top_k_rejects_shape_mismatches(
    query: np.ndarray, matrix: np.ndarray, ids: tuple[int, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        cosine_top_k(query, matrix, ids, k=2, minimum_score=0)


def test_sqlite_memory_retrieval_is_bounded_to_two_thousand_recent_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    irrelevant = np.asarray([0, 1], dtype=np.float32).tobytes()
    relevant = np.asarray([1, 0], dtype=np.float32).tobytes()
    connection.executemany(
        "INSERT INTO memories(id, embedding, embedding_dim, updated_at_utc) VALUES (?, ?, 2, ?)",
        [
            (memory_id, relevant if memory_id == 1 else irrelevant, f"{memory_id:04d}")
            for memory_id in range(1, 2_002)
        ],
    )

    hits = SqliteVectorRetriever(connection).search_memories(
        np.asarray([1, 0], dtype=np.float32), minimum_score=0.9
    )

    assert hits == ()


def test_sqlite_document_retrieval_prefers_recent_chunks_at_the_corpus_bound() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE document_chunks "
        "(id INTEGER PRIMARY KEY, embedding BLOB, embedding_dim INTEGER)"
    )
    irrelevant = np.asarray([0, 1], dtype=np.float32).tobytes()
    relevant = np.asarray([1, 0], dtype=np.float32).tobytes()
    connection.executemany(
        "INSERT INTO document_chunks(id, embedding, embedding_dim) VALUES (?, ?, 2)",
        [
            (chunk_id, relevant if chunk_id == 20_001 else irrelevant)
            for chunk_id in range(1, 20_002)
        ],
    )

    hits = SqliteVectorRetriever(connection).search_document_chunks(
        np.asarray([1, 0], dtype=np.float32), minimum_score=0.9
    )

    assert [hit.id for hit in hits] == [20_001]


@pytest.mark.model
def test_bge_model_produces_normalized_chinese_semantic_embeddings() -> None:
    root = Path(os.environ.get("VOXAGENT_DATA_ROOT", r"D:\VoxAgentData"))
    model_path = root / "models" / "embeddings" / "bge-small-zh-v1.5"
    if not model_path.is_dir():
        pytest.skip("local BGE embedding model is not installed")
    embedder = BgeSmallZhEmbedder.from_path(model_path, threads=4)

    vectors = embedder.encode(("我喜欢喝茶", "我爱喝茶", "汽车需要加油"))

    assert vectors.shape == (3, 512)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1, 1, 1], atol=1e-5)
    tea_similarity = float(np.dot(vectors[0], vectors[1]))
    car_similarity = float(np.dot(vectors[0], vectors[2]))
    assert tea_similarity > car_similarity
