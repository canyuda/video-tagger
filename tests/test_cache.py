"""Tests for hashing and the file cache."""


from sidecar.cache import FileCache
from sidecar.hashing import cache_key, sha256_file


class TestHashing:
    def test_sha256_file_stable(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"hello")
        assert sha256_file(f) == sha256_file(f)
        assert len(sha256_file(f)) == 64

    def test_sha256_file_content_sensitive(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"hello")
        (tmp_path / "b.bin").write_bytes(b"world")
        assert sha256_file(tmp_path / "a.bin") != sha256_file(tmp_path / "b.bin")

    def test_cache_key_deterministic_and_version_sensitive(self):
        k1 = cache_key("h" * 64, "classify", 1)
        assert k1 == cache_key("h" * 64, "classify", 1)
        assert k1 != cache_key("h" * 64, "classify", 2)
        assert k1 != cache_key("h" * 64, "perceive", 1)

    def test_cache_key_is_hash_not_raw_concat(self):
        assert len(cache_key("h", "classify", 1)) == 64


class TestFileCache:
    def test_put_then_get_roundtrip(self, tmp_path):
        c = FileCache(tmp_path)
        c.put("a" * 64, "classify", 1, {"type": "monologue"})
        assert c.get("a" * 64, "classify", 1) == {"type": "monologue"}

    def test_get_missing_returns_none(self, tmp_path):
        assert FileCache(tmp_path).get("b" * 64, "classify", 1) is None

    def test_version_isolates_entries(self, tmp_path):
        c = FileCache(tmp_path)
        c.put("a" * 64, "classify", 1, {"v": 1})
        c.put("a" * 64, "classify", 2, {"v": 2})
        assert c.get("a" * 64, "classify", 1) == {"v": 1}
        assert c.get("a" * 64, "classify", 2) == {"v": 2}

    def test_corrupt_entry_treated_as_missing(self, tmp_path):
        c = FileCache(tmp_path)
        path = c.put("a" * 64, "classify", 1, {"ok": True})
        path.write_text("{broken json", encoding="utf-8")
        assert c.get("a" * 64, "classify", 1) is None

    def test_sharded_layout(self, tmp_path):
        c = FileCache(tmp_path)
        path = c.put("ab" + "c" * 62, "classify", 3, {})
        assert path.parent.name == "ab" + "c" * 62
        assert path.parent.parent.name == "ab"  # shard dir
        assert path.parent.parent.parent == tmp_path
        assert path.name == "classify-v3.json"
