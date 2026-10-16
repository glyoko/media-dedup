"""The --debug report: raw fingerprints and why each file was grouped."""

from __future__ import annotations

import dataclasses

from PIL import Image

from media_dedup import cli
from media_dedup.matching import DuplicateGroup, find_exact
from media_dedup.report import _explain, debug_header, format_debug, format_group
from media_dedup.signature import DHASH_POSSIBLE, PHASH_POSSIBLE, signature_from_image
from media_factory import caption, load, write_video


def test_cli_debug_explains_every_kind_of_group(tmp_path, capsys):
    load(0).save(tmp_path / "a.jpg", quality=92)
    (tmp_path / "a copy.jpg").write_bytes((tmp_path / "a.jpg").read_bytes())
    load(0).resize((320, 240)).save(tmp_path / "a_small.jpg", quality=50)
    load(2).save(tmp_path / "t.jpg", quality=90)
    caption(load(2)).save(tmp_path / "t_caption.jpg", quality=90)
    write_video(tmp_path / "v.mp4", load(4), size=(640, 480))
    write_video(tmp_path / "v_sd.mp4", load(4))

    assert cli.main(["--dry-run", "--debug", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[debug] media-dedup" in out and f"POSSIBLE needs: pHash <= {PHASH_POSSIBLE}" in out
    assert "byte-identical" in out and "SHA-256" in out
    assert "mode RGB" in out and "pHash " in out and "dHash " in out
    assert "=> CERTAIN: every check passed" in out
    assert "FAIL local diff" in out and "=> POSSIBLE: both hashes within POSSIBLE limits" in out
    assert "frame 9:" in out and "=> CERTAIN overall" in out


def test_debug_is_shown_when_prompting(tmp_path, capsys, monkeypatch):
    load(2).save(tmp_path / "t.jpg", quality=90)
    caption(load(2)).save(tmp_path / "t_caption.jpg", quality=90)
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "a")
    assert cli.main(["-D", str(tmp_path)]) == 0
    assert "=> POSSIBLE" in capsys.readouterr().out


def test_debug_off_by_default(tmp_path, capsys):
    load(2).save(tmp_path / "t.jpg", quality=90)
    caption(load(2)).save(tmp_path / "t_caption.jpg", quality=90)
    cli.main(["--dry-run", str(tmp_path)])
    assert "[debug]" not in capsys.readouterr().out


def test_header_names_the_running_code():
    assert "media_dedup" in debug_header().splitlines()[0]


class TestVerdicts:
    def sig(self, i=0):
        return signature_from_image(load(i))

    def test_phash_too_far(self):
        a = self.sig()
        b = dataclasses.replace(a, phash=a.phash ^ ((1 << (PHASH_POSSIBLE + 1)) - 1))
        assert _explain(a, b, 0.8)[1] == f"NONE: pHash distance {PHASH_POSSIBLE + 1} > {PHASH_POSSIBLE}"

    def test_dhash_too_far(self):
        a = self.sig()
        b = dataclasses.replace(a, dhash=a.dhash ^ ((1 << (DHASH_POSSIBLE + 1)) - 1))
        assert _explain(a, b, 0.8)[1] == f"NONE: dHash distance {DHASH_POSSIBLE + 1} > {DHASH_POSSIBLE}"

    def test_low_contrast_is_named(self):
        a = self.sig()
        assert "low contrast" in _explain(a, dataclasses.replace(a, low_contrast=True), 0.8)[1]

    def test_flat_pairs(self):
        flat = signature_from_image(Image.new("RGB", (40, 30), "white"))
        assert "matched on colour only" in _explain(flat, flat, 0.8)[1]
        assert _explain(flat, self.sig(), 0.8)[1] == "NONE: only one of them is flat"


def test_video_with_undecodable_frame(tmp_path, make_media):
    a = make_media(write_video(tmp_path / "a.mp4", load(0)))
    b = make_media(write_video(tmp_path / "b.mp4", load(0), frames=150))  # longer: never compared
    b.signatures[3] = None
    text = format_debug(DuplicateGroup("uncertain", [a, b]))
    assert "9/10 frames decoded" in text
    assert "frame 3: not decoded in this file" in text
    assert "too different: never compared" in text
    assert "=> ? overall" in text


def test_exact_group_with_vanished_file(tmp_path, make_media):
    path = tmp_path / "x.jpg"
    load(0).save(path)
    (tmp_path / "y.jpg").write_bytes(path.read_bytes())
    (group,) = find_exact([make_media(p, fingerprinted=False) for p in (path, tmp_path / "y.jpg")])
    group.keeper.path.unlink()
    assert "unreadable" in format_group(group, debug=True)
