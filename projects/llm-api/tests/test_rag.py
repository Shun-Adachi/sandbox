import numpy as np
import pytest

from llm_api.config import settings
from llm_api.rag import Chunk, Index, load_chunks


def test_faq_is_split_into_one_chunk_per_question():
    chunks = load_chunks(settings.faq_path)
    assert len(chunks) == 13
    assert all(c.heading.startswith("Q.") for c in chunks)
    # 見出しだけでなく回答本文まで 1 チャンクに入っていること
    assert "A." in chunks[0].text


def test_chunking_requires_headings(tmp_path):
    path = tmp_path / "no-headings.md"
    path.write_text("見出しのない本文だけのファイル", encoding="utf-8")
    with pytest.raises(ValueError, match="見出しがありません"):
        load_chunks(path)


def _index() -> Index:
    chunks = [Chunk(id=i, heading=f"h{i}", text=f"t{i}") for i in range(3)]
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype=np.float32)
    return Index(chunks, matrix)


def test_search_returns_hits_sorted_by_score():
    hits = _index().search(np.array([1.0, 0.0], dtype=np.float32), top_k=3, threshold=0.0)
    assert [chunk.id for chunk, _ in hits] == [0, 2, 1]
    assert hits[0][1] == pytest.approx(1.0)


def test_search_drops_hits_below_the_threshold():
    hits = _index().search(np.array([1.0, 0.0], dtype=np.float32), top_k=3, threshold=0.5)
    assert [chunk.id for chunk, _ in hits] == [0, 2]


def test_search_respects_top_k():
    hits = _index().search(np.array([1.0, 0.0], dtype=np.float32), top_k=1, threshold=0.0)
    assert len(hits) == 1
