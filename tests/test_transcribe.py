import json
from pathlib import Path

import pytest

from sbob.core import frontmatter
from sbob.core.layout import Layout
from sbob.core.report import NeedsHuman
from sbob.core.text import collapse_repetitions, paragraphs
from sbob.llm.base import LLMResult
from sbob.steps import transcribe
from sbob.steps.base import StepContext
from sbob.steps.transcribe import gemini, notebooklm

S1, S2 = "2025-09-17_prova_lez01", "2025-09-18_prova_lez02"


@pytest.fixture
def audio_course(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("audio")
    for s in (S1, S2):
        (lay.audio / f"{s}.aac").write_bytes(b"fake")
    return c, lay


def test_collapse_repetitions():
    assert collapse_repetitions("ciao " + "private " * 40 + "fine") == "ciao private fine"
    assert collapse_repetitions("a b c " * 10 + "x") == "a b c x"
    assert collapse_repetitions("no no no ok") == "no no no ok"              # ripetizione legittima
    assert collapse_repetitions("пер " * 30) == "пер"
    assert collapse_repetitions("Private, private. PRIVATE! " * 8) == "Private,"
    txt = "una frase normale senza loop che deve restare uguale"
    assert collapse_repetitions(txt) == txt


def test_paragraphs():
    out = paragraphs("parola " * 200, 500)
    assert out.count("\n\n") >= 2 and "  " not in out


class FakeRole:
    label, model, n_keys = "fake/m", "m", 2

    def __init__(self, results):
        self.results, self.calls = list(results), []

    def complete(self, messages, item=None, validate=None, **kw):
        self.calls.append(item)
        return self.results.pop(0)


def test_gemini_backend_writes_frontmatter_and_skips_done(settings, audio_course, monkeypatch):
    c, lay = audio_course
    role = FakeRole([LLMResult(text="Testo uno", finish_reason="stop"), LLMResult(text="Testo due", finish_reason="stop")])
    monkeypatch.setattr(gemini.Registry, "role", lambda self, name, override=None: role)
    rep = transcribe.run(StepContext(settings, c, quiet=True))
    assert sorted(rep.done) == [S1, S2] and not rep.failed
    meta, body = frontmatter.read(lay.trascrizioni / f"{S1}.md")
    assert meta["corso"] == "Corso di Prova" and meta["backend"] == "gemini" and meta["numero"] == 1
    assert meta["sorgente"] == f"{S1}.aac" and body.strip() in ("Testo uno", "Testo due")
    assert transcribe.run(StepContext(settings, c, quiet=True)).skipped == [S1, S2]


def test_gemini_truncated_falls_back_to_segments(tmp_path, monkeypatch):
    audio = tmp_path / "a.aac"
    audio.write_bytes(b"x")
    parts = [tmp_path / "a.part000.aac", tmp_path / "a.part001.aac"]
    monkeypatch.setattr(gemini.media, "split_audio", lambda *a: parts)
    monkeypatch.setattr(gemini.media, "duration_seconds", lambda p: 2700.0)
    role = FakeRole([LLMResult(text="troncato", finish_reason="length"),
                     LLMResult(text="[00:05] prima", finish_reason="stop"), LLMResult(text="[00:05] seconda", finish_reason="stop")])
    assert gemini.transcribe_file(role, audio, "p", tmp_path, "lez") == "[00:05] prima\n\n[45:05] seconda"
    assert role.calls == ["lez", "lez#1", "lez#2"]


def test_segment_already_paid_is_reused_after_quota(tmp_path, monkeypatch):
    from sbob.core.report import QuotaExhausted
    from sbob.steps.notes.pipeline import ChunkCache
    audio = tmp_path / "a.aac"
    audio.write_bytes(b"x")
    parts = [tmp_path / "a.part000.aac", tmp_path / "a.part001.aac"]
    for p in parts:
        p.write_bytes(b"seg")
    monkeypatch.setattr(gemini.media, "split_audio", lambda *a: parts)
    monkeypatch.setattr(gemini.media, "duration_seconds", lambda p: 2700.0)
    cache = ChunkCache(tmp_path / "cache")

    class Quota(FakeRole):
        def complete(self, messages, item=None, **kw):
            self.calls.append(item)
            res = self.results.pop(0)
            if isinstance(res, Exception):
                raise res
            return res
    first = Quota([LLMResult(text="troncato", finish_reason="length"), LLMResult(text="[00:05] prima", finish_reason="stop"),
                   QuotaExhausted("finita")])
    with pytest.raises(QuotaExhausted):
        gemini.transcribe_file(first, audio, "p", tmp_path, "lez", cache)
    second = FakeRole([LLMResult(text="troncato", finish_reason="length"), LLMResult(text="[00:05] seconda", finish_reason="stop")])
    out = gemini.transcribe_file(second, audio, "p", tmp_path, "lez", cache)
    assert out == "[00:05] prima\n\n[45:05] seconda" and second.calls == ["lez", "lez#2"]      # il segmento 1 non si rifà


def test_gemini_failure_is_reported_per_file(settings, audio_course, monkeypatch):
    c, _ = audio_course
    role = FakeRole([LLMResult(error_kind="other", error="boom"), LLMResult(text="ok", finish_reason="stop")])
    monkeypatch.setattr(gemini.Registry, "role", lambda self, name, override=None: role)
    rep = transcribe.run(StepContext(settings, c, quiet=True))
    assert len(rep.done) == 1 and len(rep.failed) == 1 and rep.exit_code == 2


def test_html_import_normalizes_names(settings, tmp_path):
    pytest.importorskip("markdownify")
    c = settings.corso("prova")
    html = tmp_path / "exp"
    html.mkdir()
    (html / f"{S1}.aac.html").write_text("<p>Ciao <b>mondo</b></p>")
    rep = transcribe.run(StepContext(settings, c, quiet=True, options={"backend": "html", "html_dir": str(html)}))
    assert rep.done == [S1]
    assert "Ciao **mondo**" in (Layout.of(c).trascrizioni / f"{S1}.md").read_text()


# ------------------------------------------------------------------ notebooklm
class FakeNB:
    """Simula il CLI: le sorgenti diventano ready alla chiamata di list successiva all'add."""

    def __init__(self, fail_titles=()):
        self.sources, self.deleted, self.added, self.fail = {}, [], [], set(fail_titles)

    def __call__(self, *a):
        cmd = a[0]
        if cmd == "list":
            return json.dumps({"notebooks": [{"id": "nb1", "title": "Sbobine Corso di Prova"}]}), "", 0
        if cmd == "source" and a[1] == "list":
            for s in self.sources.values():
                s["status"] = "error" if s["title"] in self.fail else "ready"
            return json.dumps({"sources": list(self.sources.values())}), "", 0
        if cmd == "source" and a[1] == "add":
            title = Path(a[2]).name
            self.sources[title] = {"id": f"id-{title}", "title": title, "status": "processing"}
            self.added.append(title)
            return "{}", "", 0
        if cmd == "source" and a[1] == "fulltext":
            return json.dumps({"content": f"testo di {a[2]}"}), "", 0
        if cmd == "source" and a[1] == "delete":
            self.deleted.append(a[2])
            self.sources = {t: s for t, s in self.sources.items() if s["id"] != a[2]}
            return "", "", 0
        raise AssertionError(a)


def test_notebooklm_roundtrip(settings, audio_course, monkeypatch):
    c, lay = audio_course
    fake = FakeNB()
    monkeypatch.setattr(notebooklm, "_nb", fake)
    rep = transcribe.run(StepContext(settings, c, quiet=True, options={"backend": "notebooklm"}))
    # nessun sleep reale: poll=0 via monkeypatch del default
    assert sorted(rep.done) == [S1, S2] and not rep.failed
    assert (lay.trascrizioni / f"{S1}.md").read_text().strip().endswith(f"testo di id-{S1}.aac")
    assert len(fake.deleted) == 2 and not fake.sources


def test_notebooklm_gives_up_after_three_attempts(settings, audio_course, monkeypatch):
    c, _ = audio_course
    fake = FakeNB(fail_titles={f"{S1}.aac"})
    monkeypatch.setattr(notebooklm, "_nb", fake)
    rep = transcribe.run(StepContext(settings, c, quiet=True, options={"backend": "notebooklm"}))
    assert rep.done == [S2] and [f.item for f in rep.failed] == [S1]
    assert fake.added.count(f"{S1}.aac") == 3


def test_notebooklm_auth_needs_human(settings, audio_course, monkeypatch):
    c, _ = audio_course
    monkeypatch.setattr(notebooklm, "_nb", lambda *a: ("", "Not logged in, run login", 1))
    with pytest.raises(NeedsHuman) as e:
        transcribe.run(StepContext(settings, c, quiet=True, options={"backend": "notebooklm"}))
    assert e.value.action == "notebooklm login"


def test_timestamps_shift_and_strip():
    from sbob.core.text import format_ts, shift_timestamps, strip_timestamps
    assert format_ts(65) == "[01:05]" and format_ts(3725) == "[1:02:05]"
    assert shift_timestamps("[00:10] a\n\n[44:59] b", 2700) == "[45:10] a\n\n[1:29:59] b"
    assert strip_timestamps("[12:34] Allora,\n\n[1:02:03] passiamo") == "Allora,\n\npassiamo"


def test_transcribe_file_tallies_models_that_answered(tmp_path, monkeypatch):
    from sbob.steps.notes.pipeline import ModelTally, format_models
    audio = tmp_path / "a.aac"
    audio.write_bytes(b"x")
    parts = [tmp_path / "a.part000.aac", tmp_path / "a.part001.aac"]
    monkeypatch.setattr(gemini.media, "split_audio", lambda *a: parts)
    monkeypatch.setattr(gemini.media, "duration_seconds", lambda p: 2700.0)
    role = FakeRole([LLMResult(text="troncato", finish_reason="length", model="flash"),
                     LLMResult(text="uno", finish_reason="stop", model="flash"),
                     LLMResult(text="due", finish_reason="stop", model="lite")])
    tally = ModelTally()
    gemini.transcribe_file(role, audio, "p", tmp_path, "lez", tally=tally)
    assert format_models(tally.counts) == "flash ×2, lite ×1"
