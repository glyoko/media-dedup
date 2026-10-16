from __future__ import annotations

import dataclasses
import shutil

import numpy as np
from PIL import Image

from media_dedup import matching
from media_dedup.matching import compare_files, find_exact, find_similar
from media_dedup.media import MediaFile
from media_dedup.signature import Match, MatchLevel, signature_from_array
from media_factory import caption, load, recompress, write_video


def by_name(group):
    return [mf.path.name for mf in group.files]


class TestFindExact:
    def test_groups_identical_files_only(self, save, make_media, tmp_path):
        a = save(load(0), "a.jpg")
        shutil.copy(a, tmp_path / "a copy.jpg")
        shutil.copy(a, tmp_path / "z.jpg")
        save(load(1), "b.jpg")
        # Same size as a.jpg but different bytes: must not be grouped.
        (tmp_path / "same_size.jpg").write_bytes(b"x" * a.stat().st_size)
        files = [make_media(p, fingerprinted=False) for p in sorted(tmp_path.iterdir())]
        (group,) = find_exact(files)
        assert group.kind == "exact"
        assert sorted(by_name(group)) == ["a copy.jpg", "a.jpg", "z.jpg"]
        assert group.keeper.path.name != "a copy.jpg"  # copies aren't preferred

    def test_unreadable_files_are_ignored(self, save, make_media, tmp_path):
        a = save(load(0), "a.jpg")
        shutil.copy(a, tmp_path / "b.jpg")
        files = [make_media(p, fingerprinted=False) for p in sorted(tmp_path.iterdir())]
        files[1].path.unlink()
        assert find_exact(files) == []


class TestFindSimilar:
    def test_resized_and_recompressed_copies_are_similar(self, save, make_media):
        img = load(0)
        files = [
            make_media(save(img, "original.png")),
            make_media(save(img.resize((320, 240)), "small.jpg", quality=60)),
            make_media(save(recompress(img, 20), "crusty.jpg")),
            make_media(save(load(1), "unrelated.jpg")),
        ]
        (group,) = find_similar(files)
        assert group.kind == "similar"
        assert group.keeper.path.name == "original.png"
        assert sorted(by_name(group)[1:]) == ["crusty.jpg", "small.jpg"]
        assert all(m.level is MatchLevel.CERTAIN for m in group.matches.values())

    def test_edited_copy_is_uncertain(self, save, make_media):
        img = load(2)
        files = [make_media(save(img, "plain.jpg")), make_media(save(caption(img), "meme.jpg"))]
        (group,) = find_similar(files)
        assert group.kind == "uncertain"
        assert sorted(by_name(group)) == ["meme.jpg", "plain.jpg"]

    def test_uncertain_group_is_built_from_survivors(self, save, make_media):
        """A captioned copy of an image that also has a small duplicate is asked
        about against the kept original, not against the copy being removed."""
        img = load(3)
        files = [
            make_media(save(img, "orig.jpg", quality=95)),
            make_media(save(img.resize((160, 120)), "tiny.jpg")),
            make_media(save(caption(img), "meme.jpg", quality=95)),
        ]
        groups = {g.kind: g for g in find_similar(files)}
        assert by_name(groups["similar"]) == ["orig.jpg", "tiny.jpg"]
        assert sorted(by_name(groups["uncertain"])) == ["meme.jpg", "orig.jpg"]

    def test_no_duplicates(self, save, make_media):
        files = [make_media(save(load(i), f"{i}.jpg")) for i in range(5)]
        assert find_similar(files) == []

    def test_flat_images_are_only_ever_uncertain(self, save, make_media):
        files = [
            make_media(save(Image.new("RGB", (100, 100), "black"), "a.png")),
            make_media(save(Image.new("RGB", (50, 50), "black"), "b.png")),
            make_media(save(Image.new("RGB", (50, 50), "white"), "c.png")),
        ]
        (group,) = find_similar(files)
        assert group.kind == "uncertain"
        assert sorted(by_name(group)) == ["a.png", "b.png"]

    def test_chained_matches_do_not_pull_in_unrelated_files(self, monkeypatch, tmp_path):
        """A~B and B~C being CERTAIN doesn't make C a certain duplicate of A."""
        files = [_fake(tmp_path, name, pixels) for name, pixels in (("a", 300), ("b", 200), ("c", 100))]
        a, b, c = files
        levels = {
            frozenset({a, b}): MatchLevel.CERTAIN,
            frozenset({b, c}): MatchLevel.CERTAIN,
            frozenset({a, c}): MatchLevel.POSSIBLE,
        }
        monkeypatch.setattr(
            matching, "compare_files", lambda x, y: Match(levels[frozenset({x, y})], 3, 3, 1.0, 1.0)
        )
        groups = {g.kind: g for g in find_similar(files)}
        assert by_name(groups["similar"]) == ["a", "b"]
        assert by_name(groups["uncertain"]) == ["a", "c"]


def _fake(tmp_path, name: str, pixels: int) -> MediaFile:
    sig = signature_from_array(np.random.default_rng(0).integers(0, 255, (32, 32, 3), dtype=np.uint8))
    sig = dataclasses.replace(sig, phash=0, dhash=0)
    return MediaFile(
        path=tmp_path / name, root=tmp_path, kind="image", size=1, mtime=0,
        width=pixels, height=1, format="PNG", signatures=[sig],
    )


class TestVideos:
    def test_reencoded_video_is_similar(self, tmp_path, make_media):
        files = [
            make_media(write_video(tmp_path / "hd.mp4", load(0), size=(640, 480))),
            make_media(write_video(tmp_path / "sd.mp4", load(0), size=(320, 240))),
            make_media(write_video(tmp_path / "fps.mp4", load(0), size=(320, 240), frames=60, fps=30)),
            make_media(write_video(tmp_path / "other.mp4", load(1))),
        ]
        (group,) = find_similar(files)
        assert group.kind == "similar"
        assert by_name(group)[0] == "hd.mp4"
        assert sorted(by_name(group)[1:]) == ["fps.mp4", "sd.mp4"]

    def test_captioned_video_is_uncertain(self, tmp_path, make_media):
        a = make_media(write_video(tmp_path / "a.mp4", load(0)))
        b = make_media(write_video(tmp_path / "b.mp4", caption(load(0))))
        assert compare_files(a, b).level is MatchLevel.POSSIBLE

    def test_different_duration_never_matches(self, tmp_path, make_media):
        a = make_media(write_video(tmp_path / "a.mp4", load(0), frames=48))
        b = make_media(write_video(tmp_path / "b.mp4", load(0), frames=96))
        assert compare_files(a, b).level is MatchLevel.NONE
        assert find_similar([a, b]) == []

    def test_image_vs_video_never_matches(self, tmp_path, save, make_media):
        img = make_media(save(load(0), "a.png"))
        vid = make_media(write_video(tmp_path / "a.mp4", load(0)))
        assert compare_files(img, vid).level is MatchLevel.NONE

    def test_blank_videos_are_only_possible(self, tmp_path, make_media):
        black = Image.new("RGB", (320, 240), "black")
        a = make_media(write_video(tmp_path / "a.mp4", black))
        b = make_media(write_video(tmp_path / "b.mp4", black, size=(160, 120)))
        assert compare_files(a, b).level is MatchLevel.POSSIBLE

    def test_missing_frames_downgrade_to_possible(self, tmp_path, make_media):
        a = make_media(write_video(tmp_path / "a.mp4", load(0)))
        b = make_media(write_video(tmp_path / "b.mp4", load(0), size=(160, 120)))
        assert compare_files(a, b).level is MatchLevel.CERTAIN
        b.signatures[3] = None
        assert compare_files(a, b).level is MatchLevel.POSSIBLE
        b.signatures = [None] * len(b.signatures)
        assert compare_files(a, b).level is MatchLevel.NONE

    def test_unknown_durations_are_compared(self, tmp_path, make_media):
        a = make_media(write_video(tmp_path / "a.mp4", load(0)))
        b = make_media(write_video(tmp_path / "b.mp4", load(0), size=(160, 120)))
        c = make_media(write_video(tmp_path / "c.mp4", load(1)))
        a.duration = b.duration = 0.0
        (group,) = find_similar([a, b, c])
        assert sorted(by_name(group)) == ["a.mp4", "b.mp4"]
