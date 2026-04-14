import time

from app.services.cache import ContentAddressedCache


def test_put_and_get() -> None:
    cache = ContentAddressedCache()
    cache.put("key1", "value1")
    assert cache.get("key1") == "value1"


def test_miss_returns_none() -> None:
    cache = ContentAddressedCache()
    assert cache.get("nonexistent") is None


def test_lru_eviction() -> None:
    cache = ContentAddressedCache(max_size=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)  # should evict "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_lru_access_refreshes_order() -> None:
    cache = ContentAddressedCache(max_size=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # access "a" to make it most-recently used
    cache.put("c", 3)  # should evict "b", not "a"
    assert cache.get("a") == 1
    assert cache.get("b") is None


def test_ttl_expiry() -> None:
    cache = ContentAddressedCache(default_ttl_s=0.05)
    cache.put("key", "value")
    assert cache.get("key") == "value"
    time.sleep(0.06)
    assert cache.get("key") is None


def test_freshness_sensitive_uses_shorter_ttl() -> None:
    cache = ContentAddressedCache(default_ttl_s=10.0, freshness_ttl_s=0.05)
    cache.put("fresh", "data", freshness_sensitive=True)
    cache.put("stable", "data", freshness_sensitive=False)
    time.sleep(0.06)
    assert cache.get("fresh") is None  # expired
    assert cache.get("stable") == "data"  # still valid


def test_size_property() -> None:
    cache = ContentAddressedCache()
    assert cache.size == 0
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.size == 2
