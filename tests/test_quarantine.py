from __future__ import annotations

import json

from media_dedup.quarantine import DEFAULT_DIRNAME, MANIFEST_NAME, Quarantine, default_directory, find_manifest, restore


def write(path, data=b"data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_default_directory(tmp_path):
    assert default_directory([tmp_path]) == tmp_path / DEFAULT_DIRNAME
    f = write(tmp_path / "a.jpg")
    assert default_directory([f]) == tmp_path / DEFAULT_DIRNAME


def test_move_mirrors_layout_and_writes_manifest(tmp_path, make_media):
    root = tmp_path / "memes"
    kept = make_media(write(root / "keep.jpg"), root, fingerprinted=False)
    gone = make_media(write(root / "sub" / "dup.jpg", b"12345"), root, fingerprinted=False)
    q = Quarantine(root / DEFAULT_DIRNAME, [root])

    dest = q.move(gone, kept, "similar")

    assert dest == (root / DEFAULT_DIRNAME / "sub" / "dup.jpg")
    assert dest.read_bytes() == b"12345"
    assert not (root / "sub" / "dup.jpg").exists()
    (record,) = [json.loads(line) for line in q.manifest.read_text().splitlines()]
    assert record["original"] == str((root / "sub" / "dup.jpg").resolve())
    assert record["quarantined"] == str(dest.resolve())
    assert record["kept"] == str(kept.path.resolve())
    assert record["reason"] == "similar"
    assert q.bytes_moved == 5


def test_name_collisions_get_a_suffix(tmp_path, make_media):
    q = Quarantine(tmp_path / "q", [tmp_path])
    kept = make_media(write(tmp_path / "keep.jpg"), fingerprinted=False)
    first = q.move(make_media(write(tmp_path / "a.jpg"), fingerprinted=False), kept, "exact")
    second = q.move(make_media(write(tmp_path / "a.jpg"), fingerprinted=False), kept, "exact")
    third = q.move(make_media(write(tmp_path / "a.jpg"), fingerprinted=False), kept, "exact")
    assert [p.name for p in (first, second, third)] == ["a.jpg", "a (1).jpg", "a (2).jpg"]
    assert len(q.manifest.read_text().splitlines()) == 3


def test_multiple_roots_share_their_common_parent(tmp_path, make_media):
    one, two = tmp_path / "one", tmp_path / "two"
    kept = make_media(write(one / "a.jpg"), one, fingerprinted=False)
    other = make_media(write(two / "a.jpg"), two, fingerprinted=False)
    q = Quarantine(tmp_path / "q", [one, two])
    assert q.move(other, kept, "exact") == tmp_path / "q" / "two" / "a.jpg"


def test_same_path_quarantined_twice_restores_only_the_first(tmp_path, make_media):
    """The same path quarantined in two runs: only one can go back."""
    q = Quarantine(tmp_path / "q", [tmp_path])
    kept = make_media(write(tmp_path / "keep.jpg"), fingerprinted=False)
    q.move(make_media(write(tmp_path / "a.jpg", b"one"), fingerprinted=False), kept, "exact")
    q.move(make_media(write(tmp_path / "a.jpg", b"two"), fingerprinted=False), kept, "exact")
    result = restore(q.manifest)
    assert [e.status for e in result.entries] == ["restored", "taken"]
    assert (tmp_path / "a.jpg").read_bytes() == b"one"
    assert (tmp_path / "q" / "a (1).jpg").exists()
    assert len(q.manifest.read_text().splitlines()) == 1


def test_restore_recreates_deleted_folders(tmp_path, make_media):
    q = Quarantine(tmp_path / "q", [tmp_path])
    kept = make_media(write(tmp_path / "keep.jpg"), fingerprinted=False)
    q.move(make_media(write(tmp_path / "gone" / "deep" / "a.jpg"), fingerprinted=False), kept, "similar")
    (tmp_path / "gone" / "deep").rmdir()
    (tmp_path / "gone").rmdir()
    assert [e.status for e in restore(q.manifest).entries] == ["restored"]
    assert (tmp_path / "gone" / "deep" / "a.jpg").exists()
    assert not (tmp_path / "q").exists()


def test_find_manifest(tmp_path):
    assert find_manifest(tmp_path) is None
    m = write(tmp_path / DEFAULT_DIRNAME / MANIFEST_NAME)
    assert find_manifest(tmp_path) == m
    assert find_manifest(tmp_path / DEFAULT_DIRNAME) == m
    assert find_manifest(m) == m
