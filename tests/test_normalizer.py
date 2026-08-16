from app.services.normalizer import QueryNormalizer


def test_normalize_lowercases_and_strips() -> None:
    n = QueryNormalizer()
    assert n.normalize("  Hello World  ") == "hello world"


def test_normalize_preserves_word_order_and_stop_words() -> None:
    """The canonical form feeds both cache keys and router phrase matching.

    Stripping stop words or sorting tokens breaks both: multi-word patterns
    ("depends on", "how are ... organized") become unmatchable, and inverted
    relationships collapse onto one cache key.
    """
    n = QueryNormalizer()
    assert n.normalize("What services depend on the auth gateway?") == (
        "what services depend on the auth gateway"
    )


def test_normalize_does_not_collapse_inverted_relationships() -> None:
    """Regression: these are different questions with different answers."""
    n = QueryNormalizer()
    assert n.normalize("does billing depend on the warehouse") != n.normalize(
        "does the warehouse depend on billing"
    )


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


def test_keywords_strip_stop_words_but_keep_order() -> None:
    n = QueryNormalizer()
    assert n.keywords("What is the latest status of the rollout?") == [
        "latest",
        "status",
        "rollout",
    ]


def test_fingerprint_is_order_insensitive() -> None:
    n = QueryNormalizer()
    a = n.fingerprint("How are service A and service B related?")
    b = n.fingerprint("service B and service A related how are?")
    assert a == b


def test_content_key_deterministic() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default") == n.content_key(nq, "default")
    assert len(n.content_key(nq, "default")) == 64  # SHA-256 hex


def test_content_key_varies_by_dataset() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "dataset_a") != n.content_key(nq, "dataset_b")


def test_content_key_varies_by_freshness_contract() -> None:
    """A cached non-fresh answer must not satisfy a request demanding freshness."""
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default", freshness_required=False) != n.content_key(
        nq, "default", freshness_required=True
    )


def test_content_key_varies_by_node_set_scope() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default", node_sets=["billing"]) != n.content_key(
        nq, "default", node_sets=["auth"]
    )


def test_content_key_node_set_order_does_not_matter() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default", node_sets=["a", "b"]) == n.content_key(
        nq, "default", node_sets=["b", "a"]
    )


def test_content_key_varies_by_model() -> None:
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default", model="m1") != n.content_key(nq, "default", model="m2")


def test_content_key_varies_by_policy_version() -> None:
    """A routing/prompt policy change must invalidate previously cached answers."""
    n = QueryNormalizer()
    nq = n.normalize("test query")
    assert n.content_key(nq, "default", policy_version="1") != n.content_key(
        nq, "default", policy_version="2"
    )


# --- the paraphrase form: a losable collision table ---------------------------
# Each pair encodes a promise. MUST_COLLIDE pairs differ only in function words
# or morphology — serving one from the other's cache entry is correct.
# MUST_NOT_COLLIDE pairs differ in meaning — a collision would serve a wrong
# answer at cache speed, which is the worst failure mode this system has.

MUST_COLLIDE = [
    ("Does billing depend on the warehouse?", "does billing depend on warehouse"),
    ("Which team owns the notification service?", "which teams own the notification services"),
    ("Is the checkout service linked to billing?", "is checkout service linked to billing"),
    ("did the scheduler call the job queue", "does a scheduler call the job queue"),
]

MUST_NOT_COLLIDE = [
    # Inversion: same words, opposite question.
    ("Does billing depend on the warehouse?", "Does the warehouse depend on billing?"),
    # Negation: "not" is deliberately not a stop word.
    ("Is billing connected to the warehouse?", "Is billing not connected to the warehouse?"),
    # Different entity.
    ("Does billing depend on the warehouse?", "Does billing depend on the ledger?"),
    # Different relation.
    ("Does billing depend on the warehouse?", "Does billing write to the warehouse?"),
]


def test_paraphrase_form_collides_exactly_where_promised() -> None:
    n = QueryNormalizer()
    for a, b in MUST_COLLIDE:
        assert n.paraphrase_form(a) == n.paraphrase_form(b), (a, b)
    for a, b in MUST_NOT_COLLIDE:
        assert n.paraphrase_form(a) != n.paraphrase_form(b), (a, b)


def test_paraphrase_key_is_disjoint_from_exact_keys() -> None:
    """Even a stopword-free, unstemmable query must not cross namespaces."""
    n = QueryNormalizer()
    q = "ledger warehouse"
    assert n.paraphrase_form(q) == n.normalize(q)  # the collision precondition
    assert n.paraphrase_content_key(q, "default") != n.content_key(n.normalize(q), "default")


def test_paraphrase_key_preserves_scope_model_and_caller_boundaries() -> None:
    n = QueryNormalizer()
    q = "does billing depend on warehouse"
    assert n.paraphrase_content_key(q, "ds_a") != n.paraphrase_content_key(q, "ds_b")
    assert n.paraphrase_content_key(q, "d", node_sets=["a"]) != n.paraphrase_content_key(
        q, "d", node_sets=["b"]
    )
    assert n.paraphrase_content_key(q, "d", model="m1") != n.paraphrase_content_key(
        q, "d", model="m2"
    )
    assert n.paraphrase_content_key(q, "d", user_id="u1") != n.paraphrase_content_key(
        q, "d", user_id="u2"
    )
