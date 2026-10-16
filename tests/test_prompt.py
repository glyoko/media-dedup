from __future__ import annotations

from pathlib import Path

import pytest

from media_dedup.matching import DuplicateGroup
from media_dedup.media import MediaFile
from media_dedup.prompt import QuitRequested, ask, open_files, parse_choice
from media_dedup.signature import Match, MatchLevel


def group(n=3) -> DuplicateGroup:
    files = [
        MediaFile(path=Path(f"/x/f{i}.jpg"), root=Path("/x"), kind="image", size=1000 * (n - i),
                  mtime=0, width=10, height=10, format="JPEG")
        for i in range(n)
    ]
    return DuplicateGroup("uncertain", files, {f: Match(MatchLevel.POSSIBLE, 8) for f in files[1:]})


def run(answers, g=None):
    g = g or group()
    answers = iter(answers)
    out, opened = [], []

    def fake_input(_prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    keep = ask(g, input_fn=fake_input, output=out.append, opener=opened.extend)
    return g, keep, "\n".join(out), opened


@pytest.mark.parametrize(
    "answer, expected",
    [("2", [1]), ("1,3", [0, 2]), ("3 1", [0, 2]), ("2,2", [1]), ("", None), ("0", None),
     ("4", None), ("x", None), ("1,x", None)],
)
def test_parse_choice(answer, expected):
    assert parse_choice(answer, 3) == expected


def test_enter_keeps_suggestion():
    g, keep, out, _ = run([""])
    assert keep == [g.keeper]
    assert "<- suggested" in out
    assert "87.5% similar" in out


def test_numbers_pick_files():
    g, keep, _, _ = run(["2,3"])
    assert keep == g.files[1:]


def test_keep_all():
    g, keep, _, _ = run(["a"])
    assert keep == g.files


def test_view_then_choose():
    g, keep, _, opened = run(["v", "1"])
    assert opened == g.files
    assert keep == [g.keeper]


def test_help_and_invalid_input_reprompt():
    _, keep, out, _ = run(["?", "banana", "9", "2"])
    assert "keep all of them" in out
    assert "Didn't understand 'banana'" in out
    assert keep[0].path.name == "f1.jpg"


@pytest.mark.parametrize("answers", [["q"], []])
def test_quit_and_eof(answers):
    with pytest.raises(QuitRequested):
        run(answers)


def test_default_input_is_builtin(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    g = group()
    assert ask(g, output=lambda _: None) == [g.files[1]]


def test_open_files_uses_platform_opener(monkeypatch):
    calls = []
    monkeypatch.setattr("media_dedup.prompt.sys.platform", "darwin")
    monkeypatch.setattr("media_dedup.prompt.subprocess.run", lambda cmd, check: calls.append(cmd))
    open_files(group(2).files)
    assert calls == [["open", "/x/f0.jpg", "/x/f1.jpg"]]
