from app.services.normalizer import QueryNormalizer


def test_normalize_lowercases_and_strips() -> None:
    n = QueryNormalizer()
    assert n.normalize("  Hello World  ") == "hello world"


def test_normalize_removes_stop_words() -> None:
    n = QueryNormalizer()
    result = n.normalize("What is the latest status of the rollout?")
    assert "the" not in result.split()
    assert "is" not in result.split()
    assert "of" not in result.split()


def test_normalize_sorts_keywords() -> None:
    n = QueryNormalizer()
    a = n.normalize("How are service A and service B related?")
    b = n.normalize("service B and service A related how are?")
    assert a == b


def test_normalize_strips_punctuation() -> None:
    n = QueryNormalizer()
    result = n.normalize("What's the status?!")
    assert "?" not in result
    assert "!" not in result
    assert "'" not in result


def test_normalize_identical_queries_same_output() -> None:
    n = QueryNormalizer()
    q1 = "What is the latest status of the rollout?"
    q2 = "what is the latest status of the rollout"
    assert n.normalize(q1) == n.normalize(q2)


def test_content_key_deterministic() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    key1 = n.content_key(nq, "default")
    key2 = n.content_key(nq, "default")
    assert key1 == key2
    assert len(key1) == 64  # SHA-256 hex


def test_content_key_varies_by_dataset() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    k1 = n.content_key(nq, "dataset_a")
    k2 = n.content_key(nq, "dataset_b")
    assert k1 != k2


def test_content_key_varies_by_mode() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    k1 = n.content_key(nq, "default", mode="CHUNKS")
    k2 = n.content_key(nq, "default", mode="TEMPORAL")
    assert k1 != k2
