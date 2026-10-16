"""End-to-end tests of the command line tool on a realistic messy folder."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
from PIL import Image

from media_dedup import cli
from media_dedup.report import describe, human_size
from media_dedup.signature import DHASH_POSSIBLE
from media_factory import caption, load, write_video

Q = ".media-dedup-removed"


@pytest.fixture
def memes(tmp_path):
    """A folder with every kind of duplicate in it."""
    root = tmp_path / "memes"
    root.mkdir()
    load(0).save(root / "doge.jpg", quality=92)
    (root / "from discord").mkdir()
    shutil.copy(root / "doge.jpg", root / "from discord" / "doge (1).jpg")  # exact
    load(0).resize((320, 240)).save(root / "from discord" / "doge_small.jpg", quality=50)  # similar
    load(1).save(root / "cat.png")
    load(1).resize((160, 120)).save(root / "cat_thumb.jpg", quality=70)  # similar
    load(2).save(root / "template.jpg", quality=90)
    caption(load(2)).save(root / "template_captioned.jpg", quality=90)  # uncertain
    load(3).save(root / "unique.jpg")
    write_video(root / "clip.mp4", load(4), size=(640, 480))
    write_video(root / "clip_sd.mp4", load(4), size=(320, 240))  # similar video
    (root / "notes.txt").write_text("not media")
    (root / "corrupt.jpg").write_bytes(b"this is not an image")
    return root


def files_in(root):
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and Q not in p.parts
    )


def run(args, capsys):
    code = cli.main([str(a) for a in args])
    out, err = capsys.readouterr()
    return code, out, err


ORIGINAL = [
    "cat.png", "cat_thumb.jpg", "clip.mp4", "clip_sd.mp4", "corrupt.jpg", "doge.jpg",
    "from discord/doge (1).jpg", "from discord/doge_small.jpg", "notes.txt",
    "template.jpg", "template_captioned.jpg", "unique.jpg",
]
AUTO_REMOVED = {"cat_thumb.jpg", "clip_sd.mp4", "from discord/doge (1).jpg", "from discord/doge_small.jpg"}


def test_dry_run_changes_nothing(memes, capsys):
    code, out, err = run(["--dry-run", memes], capsys)
    assert code == 0
    assert files_in(memes) == ORIGINAL
    assert not (memes / Q).exists()
    assert "Identical files (2 files)" in out
    assert "Same image" in out and "Same video" in out
    assert "Would ask about these" in out
    assert "Dry run: would remove 4 files" in out
    assert "1 group would need your decision" in out
    assert "Found 9 images and 2 videos." in err
    assert "Skipped 1 file that couldn't be read" in err


def test_non_interactive_removes_only_certain_duplicates(memes, capsys):
    code, out, _ = run(["-n", memes], capsys)
    assert code == 0
    assert set(files_in(memes)) == set(ORIGINAL) - AUTO_REMOVED
    assert "Not sure about these; leaving them alone" in out
    assert "Moved 4 files" in out
    assert "run in a terminal without -n to decide" in out

    quarantined = sorted(str(p.relative_to(memes / Q)) for p in (memes / Q).rglob("*.*"))
    assert quarantined == sorted(AUTO_REMOVED | {"manifest.jsonl"})
    records = [json.loads(line) for line in (memes / Q / "manifest.jsonl").read_text().splitlines()]
    kept = {r["original"].split("/memes/")[1]: (r["kept"].split("/memes/")[1], r["reason"]) for r in records}
    assert kept == {
        "from discord/doge (1).jpg": ("doge.jpg", "exact"),
        "from discord/doge_small.jpg": ("doge.jpg", "similar"),
        "cat_thumb.jpg": ("cat.png", "similar"),
        "clip_sd.mp4": ("clip.mp4", "similar"),
    }


def test_second_run_finds_nothing_and_ignores_quarantine(memes, capsys):
    run(["-n", memes], capsys)
    code, out, _ = run(["-n", memes], capsys)
    assert code == 0
    assert "Moved" not in out
    assert "Nothing was removed." in out
    assert set(files_in(memes)) == set(ORIGINAL) - AUTO_REMOVED


@pytest.mark.parametrize(
    "answer, removed",
    [
        # Same resolution, so the (larger) captioned file is the suggestion.
        ("", "template.jpg"),
        ("2", "template_captioned.jpg"),
        ("a", None),
    ],
)
def test_interactive_decisions(memes, capsys, monkeypatch, answer, removed):
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    code, out, _ = run([memes], capsys)
    assert code == 0
    expected = set(ORIGINAL) - AUTO_REMOVED - ({removed} if removed else set())
    assert set(files_in(memes)) == expected
    assert "undecided" not in out


def test_interactive_quit_leaves_group(memes, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    _, out, _ = run([memes], capsys)
    assert set(files_in(memes)) == set(ORIGINAL) - AUTO_REMOVED
    assert "Leaving the remaining possible duplicates alone." in out
    assert "1 group of possible duplicates left undecided." in out


def test_suggested_keeper_in_uncertain_group(memes, capsys):
    _, out, _ = run(["-d", memes], capsys)
    ask_block = out.split("Would ask about these:")[1]
    first = ask_block.splitlines()[2]
    assert "template_captioned.jpg" in first and "<- suggested" in first


def test_no_recursive(memes, capsys):
    run(["-n", "--no-recursive", memes], capsys)
    assert (memes / "from discord" / "doge (1).jpg").exists()
    assert (memes / "from discord" / "doge_small.jpg").exists()
    assert not (memes / "cat_thumb.jpg").exists()


def test_checksum_only_removes_just_identical_copies(memes, capsys, monkeypatch):
    def no_decoding(*_args, **_kwargs):
        raise AssertionError("--checksum-only must not analyse images or videos")

    monkeypatch.setattr(cli, "fingerprint_all", no_decoding)
    code, out, err = run(["--checksum-only", memes], capsys)
    assert code == 0
    assert set(files_in(memes)) == set(ORIGINAL) - {"from discord/doge (1).jpg"}
    assert "Identical files (2 files)" in out
    assert "Possible duplicates" not in out and "Same image" not in out
    assert "couldn't be read" not in err  # corrupt.jpg is never opened


def test_checksum_only_dry_run(memes, capsys, monkeypatch):
    monkeypatch.setattr(cli, "fingerprint_all", None)
    _, out, _ = run(["-c", "-d", memes], capsys)
    assert files_in(memes) == ORIGINAL
    assert "Dry run: would remove 1 file " in out


def test_checksum_only_no_duplicates(tmp_path, capsys):
    load(0).save(tmp_path / "a.jpg")
    load(0).resize((320, 240)).save(tmp_path / "b.jpg")  # similar, but not identical
    _, out, _ = run(["-c", tmp_path], capsys)
    assert out.strip() == "No duplicates found."


def test_custom_quarantine_dir(memes, tmp_path, capsys):
    qdir = tmp_path / "trash"
    run(["-n", "-q", qdir, memes], capsys)
    assert (qdir / "manifest.jsonl").exists()
    assert (qdir / "cat_thumb.jpg").exists()


def test_verbose_lists_unreadable_files(memes, capsys):
    _, _, err = run(["-d", "-v", memes], capsys)
    assert "corrupt.jpg" in err


def test_no_duplicates(tmp_path, capsys):
    for i in range(3):
        load(i).save(tmp_path / f"{i}.jpg")
    code, out, _ = run([tmp_path], capsys)
    assert code == 0
    assert out.strip() == "No duplicates found."


def test_empty_folder(tmp_path, capsys):
    code, out, err = run([tmp_path], capsys)
    assert code == 0
    assert "Found 0 images and 0 videos." in err


def test_move_failures_are_reported(memes, capsys, monkeypatch):
    def fail(*_args):
        raise PermissionError("read-only")

    monkeypatch.setattr("media_dedup.quarantine.Quarantine.move", fail)
    code, out, err = run(["-n", memes], capsys)
    assert code == 0
    assert "Couldn't move" in err and "read-only" in err
    assert "Nothing was removed." in out


@pytest.mark.parametrize("args", [["/does/not/exist"], ["--jobs", "0", "."], []])
def test_bad_arguments(args, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_progress_is_shown_on_a_terminal(memes, capsys, monkeypatch):
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    _, _, err = run(["-d", memes], capsys)
    assert "Analysing" in err


def test_module_entry_point(memes):
    result = subprocess.run(
        [sys.executable, "-m", "media_dedup", "--dry-run", str(memes)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout


def test_report_helpers(make_media, save):
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024**4) == "5120.0 GB"
    img = make_media(save(Image.new("RGB", (4, 3)), "x.png"))
    assert describe(img) == f"4x3, PNG, {human_size(img.size)}"


class TestRestore:
    def test_restores_everything_including_identical_copies(self, memes, capsys):
        run(["-n", memes], capsys)
        code, out, _ = run(["restore", memes], capsys)
        assert code == 0
        assert files_in(memes) == ORIGINAL
        assert "from discord/doge (1).jpg" in out  # the byte-identical copy
        assert "Restored 4 files." in out
        assert not (memes / Q).exists()  # emptied quarantine is cleaned up

    def test_undo_hint_after_moving(self, memes, capsys):
        _, out, _ = run(["-n", memes], capsys)
        assert "To undo: media-dedup restore" in out

    def test_dry_run_changes_nothing(self, memes, capsys):
        run(["-n", memes], capsys)
        manifest = (memes / Q / "manifest.jsonl").read_text()
        code, out, _ = run(["restore", "--dry-run", memes / Q], capsys)
        assert code == 0
        assert out.count("Would restore") == 4
        assert "Dry run: would restore 4 files." in out
        assert set(files_in(memes)) == set(ORIGINAL) - AUTO_REMOVED
        assert (memes / Q / "manifest.jsonl").read_text() == manifest

    def test_only_restores_matching_files_and_keeps_the_rest_listed(self, memes, capsys):
        run(["-n", memes], capsys)
        code, out, _ = run(["restore", "--only", "cat*", "-o", "from discord/doge_small.jpg", memes], capsys)
        assert code == 0
        assert "Restored 2 files." in out
        assert set(files_in(memes)) == set(ORIGINAL) - {"clip_sd.mp4", "from discord/doge (1).jpg"}
        records = [json.loads(line) for line in (memes / Q / "manifest.jsonl").read_text().splitlines()]
        assert sorted(r["original"].split("/memes/")[1] for r in records) == [
            "clip_sd.mp4", "from discord/doge (1).jpg",
        ]

    def test_no_match(self, memes, capsys):
        run(["-n", memes], capsys)
        code, out, _ = run(["restore", "-o", "nope*", memes], capsys)
        assert code == 1
        assert "Nothing in the quarantine matches." in out

    def test_never_overwrites_and_reports_missing_files(self, memes, capsys):
        run(["-n", memes], capsys)
        (memes / "cat_thumb.jpg").write_bytes(b"new file at the old path")
        (memes / Q / "clip_sd.mp4").unlink()
        (memes / Q / "manifest.jsonl").open("a").write("not json\n")
        code, out, err = run(["restore", memes / Q / "manifest.jsonl"], capsys)
        assert code == 1
        assert (memes / "cat_thumb.jpg").read_bytes() == b"new file at the old path"
        assert "something is already at" in err and "cat_thumb.jpg" in err
        assert "Missing from the quarantine" in err and "clip_sd.mp4" in err
        assert "Ignored 1 unreadable manifest line." in err
        assert "Restored 2 files; 2 couldn't be (see above)." in out
        # Whatever wasn't restored stays listed, so nothing is forgotten.
        assert len((memes / Q / "manifest.jsonl").read_text().splitlines()) == 3
        assert (memes / Q / "cat_thumb.jpg").exists()

    def test_move_failures_are_reported(self, memes, capsys, monkeypatch):
        run(["-n", memes], capsys)

        def fail(*_args):
            raise PermissionError("read-only")

        monkeypatch.setattr("media_dedup.quarantine.shutil.move", fail)
        code, out, err = run(["restore", memes], capsys)
        assert code == 1
        assert err.count("read-only") == 4
        assert "Restored 0 files" in out
        assert len((memes / Q / "manifest.jsonl").read_text().splitlines()) == 4

    def test_empty_manifest(self, tmp_path, capsys):
        (tmp_path / "manifest.jsonl").write_text("\n")
        code, out, _ = run(["restore", tmp_path], capsys)
        assert code == 0
        assert "The quarantine manifest is empty." in out

    def test_no_manifest_is_an_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["restore", str(tmp_path)])
        assert exc.value.code == 2
        assert "no quarantine manifest" in capsys.readouterr().err

    def test_folder_named_restore_can_still_be_scanned(self, tmp_path, capsys, monkeypatch):
        (tmp_path / "restore").mkdir()
        monkeypatch.chdir(tmp_path)
        code, out, err = run(["-d", "./restore"], capsys)
        assert code == 0 and "Found 0 images" in err


@pytest.mark.parametrize("flag", ["--phash", "--dhash"])
def test_hash_limit_flags_tighten_possible_matches(memes, capsys, flag):
    _, out, _ = run(["-d", memes], capsys)
    assert "Would ask about these" in out
    _, out, _ = run(["-d", flag, "2", memes], capsys)
    assert "Would ask about these" not in out  # caption edit is ~6 apart in both hashes


def test_hash_limit_flags_show_in_debug_header(memes, capsys):
    _, out, _ = run(["-d", "-D", "--phash", "9", memes], capsys)
    header = next(line for line in out.splitlines() if "POSSIBLE needs" in line)
    assert header.startswith(f"[debug] POSSIBLE needs: pHash <= 9, dHash <= {DHASH_POSSIBLE} ")
    assert header.endswith("[set by --phash]")
    assert "possible <= 9)" in out
    _, out, _ = run(["-d", "-D", memes], capsys)
    assert "set by" not in out


@pytest.mark.parametrize("value", ["-1", "65", "ten"])
def test_bad_hash_limits(value, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--phash", value, "."])
    assert exc.value.code == 2
    assert "--phash" in capsys.readouterr().err
