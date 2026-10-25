from __future__ import annotations

import math

from hushbrain.embeddings import HashEmbedder, OllamaEmbedder, vec_literal


def cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashEmbedder(64)
    v = e.embed("peat free compost")
    assert v == e.embed("peat free compost")
    assert len(v) == 64
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_hash_embedder_never_returns_a_zero_vector():
    for text in ("", "   ", "!!!"):
        assert any(x != 0.0 for x in HashEmbedder(64).embed(text))


def test_shared_words_raise_similarity():
    e = HashEmbedder(256)
    q = e.embed("supplier lead time")
    assert cos(q, e.embed("supplier lead time in March")) > cos(q, e.embed("family weekend plans"))


def test_vec_literal_shape():
    assert vec_literal([1.0, 0.5]) == "[1.000000,0.500000]"


def test_ollama_truncates_at_a_paragraph_boundary():
    e = OllamaEmbedder("http://localhost:1", "m", 4)
    text = ("a" * 4500) + "\n\n" + ("b" * 4000)
    cut = e._truncate(text)
    assert len(cut) == 4500
    assert e._truncate("short") == "short"
