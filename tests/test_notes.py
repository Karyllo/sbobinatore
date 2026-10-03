import importlib.util
import sys
from pathlib import Path

import pytest

from sbob.core import frontmatter
from sbob.core.layout import Layout
from sbob.core.status import course_status
from sbob.llm.base import ErrorKind, LLMResult
from sbob.steps import notes as notes_mod
from sbob.steps.base import StepContext
from sbob.steps.notes import chunker, pipeline

STEM = "2025-09-17_prova_lez01"
# chunker dello script originale (solo sulla macchina dell'autore; altrove il test viene saltato)
OLD_CHUNKER = Path.home() / "Desktop/Karyl/script/python/sbobine universitarie copy/chunker.py"


@pytest.mark.skipif(not OLD_CHUNKER.exists(), reason="vecchio chunker non disponibile")
def test_chunker_identical_to_original(monkeypatch):
    sys.modules["config"] = type(sys)("config")
    sys.modules["config"].CHUNK_SIZE_WORDS = 850
    spec = importlib.util.spec_from_file_location("old_chunker", OLD_CHUNKER)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    text = " ".join(f"w{i}" for i in range(5000)) + "\n\n  spazi   strani \t fine"
    assert chunker.chunk_text(text, 850) == old.chunk_text(text)
    assert chunker.chunk_text("", 850) == [] or chunker.chunk_text("   ") == []


def test_chunker_sizes():
    chunks = chunker.chunk_text("a " * 2000, 850)
    assert len(chunks) == 3 and max(len(c.split()) for c in chunks) - min(len(c.split()) for c in chunks) <= 1


def test_wall_of_text_validator():
    assert pipeline.wall_of_text("x" * 600) == "muro di testo"
    assert pipeline.wall_of_text("x" * 600 + "\n" * 6) is None
    assert pipeline.wall_of_text("breve") is None


def test_split_topics():
    body, topics = pipeline.split_topics("## T\n\ntesto\n\nARGOMENTI: diodi; BJT ; transistor.\n", "it")
    assert "ARGOMENTI" not in body and body.startswith("## T") and topics == ["diodi", "BJT", "transistor"]
    assert pipeline.split_topics("solo testo", "it") == ("solo testo\n", [])
    assert pipeline.split_topics("x\n**TOPICS:** a; b", "en")[1] == ["a", "b"]


class FakeRole:
    label, model, workers, n_keys, used_fallback, provider_name = "fake/m", "m", 1, 1, None, "fake"

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def complete(self, messages, item=None, validate=None, **kw):
        self.calls.append(item)
        return self.fn(messages[0].parts[0].text, item)


def _good(text, item):
    return LLMResult(text="## Titolo\n\nparagrafo\n\n" + "riga\n" * 6 + "ARGOMENTI: uno; due")


def test_generate_notes_refiner_fallback_and_topics():
    refiner = FakeRole(lambda t, i: LLMResult(error_kind=ErrorKind.OTHER, error="x"))
    notes = FakeRole(_good)
    raw = " ".join(f"p{i}" for i in range(1800))
    text, topics, errors = pipeline.generate_notes(raw, refiner, notes, "it", "lez", 850)
    assert errors == [] and topics == ["uno", "due"] and text.count(pipeline.SEPARATOR) == 2
    assert "ARGOMENTI" not in text
    # il refiner è fallito → alle notes arriva il testo originale del chunk
    assert len(notes.calls) == 3 and len(refiner.calls) == 3


def test_run_writes_appunti_and_partial_on_failure(settings, monkeypatch):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("trascrizioni")
    (lay.trascrizioni / f"{STEM}.md").write_text(frontmatter.join({"backend": "gemini"}, "parola " * 100))

    class Reg:
        def __init__(self, settings, tracker): self.tracker = tracker
        def role(self, name, override=None):
            return REFINER if name == "refiner" else NOTES

    REFINER = FakeRole(lambda t, i: LLMResult(text="raffinato"))
    NOTES = FakeRole(lambda t, i: LLMResult(error_kind=ErrorKind.SERVER, error="boom"))
    monkeypatch.setattr(notes_mod, "Registry", Reg)

    rep = notes_mod.run(StepContext(settings, c, quiet=True))
    assert [f.item for f in rep.failed] == [STEM] and rep.done == []
    assert (lay.appunti / f"{STEM}.parziale.md").exists() and not (lay.appunti / f"{STEM}_appunti.md").exists()
    assert course_status(c)["totali"]["appunti"] == 0          # un parziale non conta come fatto

    NOTES.fn = _good                                           # secondo giro: riesce → sostituisce il parziale
    rep = notes_mod.run(StepContext(settings, c, quiet=True))
    assert rep.done == [STEM] and not (lay.appunti / f"{STEM}.parziale.md").exists()
    meta, body = frontmatter.read(lay.appunti / f"{STEM}_appunti.md")
    assert "argomenti" not in meta and meta["backend"] == "gemini" and meta["modello"] == "m"
    assert "ARGOMENTI" not in body
    assert "## Titolo" in body and course_status(c)["totali"]["appunti"] == 1
    assert notes_mod.run(StepContext(settings, c, quiet=True)).skipped == [STEM]


def test_dry_run_estimates(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("trascrizioni")
    (lay.trascrizioni / f"{STEM}.md").write_text("parola " * 1800)
    rep = notes_mod.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert rep.done == [STEM] and "1800 parole in 3 blocchi" in rep.notes[0]


def test_chunk_cache_resumes_after_interruption(tmp_path):
    from sbob.core.report import NeedsHuman

    raw = " ".join(f"p{i}" for i in range(2600))            # 4 blocchi
    refiner = FakeRole(lambda t, i: LLMResult(text=t))        # restituisce un testo lungo quanto l'input
    calls = {"n": 0}

    def flaky(text, item):
        calls["n"] += 1
        if calls["n"] == 3:
            raise NeedsHuman("quota")
        return _good(text, item)
    notes = FakeRole(flaky)
    cache = pipeline.ChunkCache(tmp_path / "cache")
    with pytest.raises(NeedsHuman):
        pipeline.generate_notes(raw, refiner, notes, "it", "lez", 850, cache=cache)
    assert len(list((tmp_path / "cache").glob("notes_*.md"))) == 2

    refiner.calls.clear()
    notes.calls.clear()
    notes.fn = _good
    text, topics, errors = pipeline.generate_notes(raw, refiner, notes, "it", "lez", 850, cache=cache)
    assert not errors and refiner.calls == [] and sorted(notes.calls) == ["lez#3", "lez#4"]


def test_too_short_block_is_rejected_and_refiner_shrink_falls_back():
    raw = " ".join(f"p{i}" for i in range(800))
    seen = []

    class Role(FakeRole):
        def complete(self, messages, item=None, validate=None, **kw):
            seen.append(validate)
            return super().complete(messages, item, validate, **kw)
    refiner = Role(lambda t, i: LLMResult(text="troppo corto"))           # il refiner "riassume" → si usa l'originale
    notes = Role(lambda t, i: LLMResult(text="."))
    v = pipeline.notes_validator(raw)                                      # 800 parole → ne servono almeno 400
    assert "troppo corto" in v(".")
    assert "muro di testo" in v("parola " * 450)                           # abbastanza lungo ma senza a capo
    assert v("\n".join(["parola " * 60] * 8)) is None                      # 480 parole con a capo: ok
    assert pipeline.refine_chunk(refiner, 0, [raw], "it", "lez") == raw


def test_verify_finds_empty_block(settings):
    from sbob.core.verify import verify_course
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("trascrizioni", "appunti")
    (lay.trascrizioni / f"{STEM}.md").write_text("parola " * 900)
    body = "\n---\n".join(["testo " * 300, "", "testo " * 300])
    (lay.appunti / f"{STEM}_appunti.md").write_text(body)
    probs = [i["problema"] for i in verify_course(c, check_audio=False)["problemi"]]
    assert any("blocchi quasi vuoti" in p and "[2]" in p for p in probs)


def test_provider_prompt_variant():
    from sbob.core import prompts
    base = prompts.load("it", "notes")
    assert prompts.load("it", "notes", "deepseek") != base and "prima persona" in prompts.load("it", "notes", "deepseek")
    assert prompts.load("it", "notes", "gemini") == base                    # nessuna variante → prompt base
    out = prompts.render("it", "notes", variante="deepseek", chunk_text="X", part_number=1, total_parts=2)
    assert "Parte 1 di 2" in out and "$x_{ij}$" in out                       # le graffe LaTeX sopravvivono a format


def test_normalize_math_converts_latex_delimiters_but_not_code():
    from sbob.core.text import normalize_math
    src = "Sia \\( y \\) reale e\n\\[\nx \\ge 0\n\\]\ne ancora \\[ x^n = y \\].\n```\n\\( resta \\)\n```\nGià $a$ e $$b$$."
    out = normalize_math(src)
    assert "Sia $y$ reale" in out and "$$\nx \\ge 0\n$$" in out and "ancora $$x^n = y$$." in out
    assert "```\n\\( resta \\)\n```" in out and "Già $a$ e $$b$$." in out
    assert normalize_math(out) == out                                  # idempotente


def test_header_reports_models_that_actually_answered(settings, monkeypatch):
    """Principale e riserva rispondono a blocchi diversi: l'intestazione li elenca entrambi, con i conteggi."""
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("trascrizioni")
    (lay.trascrizioni / f"{STEM}.md").write_text(frontmatter.join({"backend": "gemini"}, "p " * 1800))   # 3 blocchi

    def notes_answer(text, item):
        model = "m-principale" if item.endswith("#1") else "m-lite"      # solo il primo blocco va al principale
        return LLMResult(text="## Titolo\n\nparagrafo\n\n" + "riga\n" * 6, model=model)

    class Reg:
        def __init__(self, settings, tracker): self.tracker = tracker
        def role(self, name, override=None):
            return REFINER if name == "refiner" else NOTES

    REFINER = FakeRole(lambda t, i: LLMResult(text=t, model="m-lite"))
    NOTES = FakeRole(notes_answer)
    NOTES.model = "m-principale"
    NOTES.used_fallback = "fake/m-lite"      # l'ex intestazione avrebbe scritto sempre "(+ riserva ...)"
    monkeypatch.setattr(notes_mod, "Registry", Reg)

    rep = notes_mod.run(StepContext(settings, c, quiet=True))
    assert rep.done == [STEM]
    meta, _ = frontmatter.read(lay.appunti / f"{STEM}_appunti.md")
    assert meta["modello"] == "m-lite ×2, m-principale ×1"
    assert meta["refiner"] == "m-lite"


def test_format_models():
    from collections import Counter
    assert pipeline.format_models(Counter({"a": 3})) == "a"
    assert pipeline.format_models(Counter({"b": 9, "a": 3})) == "a ×3, b ×9"
    assert pipeline.format_models(Counter(), "x") == "x"


def _write_costs(lay, rows):
    import json
    lay.state.mkdir(parents=True, exist_ok=True)
    lay.costs.write_text("\n".join(json.dumps({"role": "notes", "item": f"{STEM}#{i}", "model": m, "error": e})
                                   for i, m, e in rows) + "\n")


def test_verify_compares_header_with_logs_and_flags_reserve_lessons(settings):
    from sbob.core import frontmatter
    from sbob.core.verify import models_from_log, verify_course
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("trascrizioni", "appunti")
    (lay.trascrizioni / f"{STEM}.md").write_text("parola " * 600)
    meta = frontmatter.lesson_meta(c, STEM, modello="principale-1")
    (lay.appunti / f"{STEM}_appunti.md").write_text(frontmatter.join(meta, "testo " * 600))
    # blocco 1: il principale fallisce per quota, poi risponde la riserva; blocchi 2 e 3: riserva
    _write_costs(lay, [(1, "principale-1", "quota"), (1, "riserva-1", None), (2, "riserva-1", None), (3, "riserva-1", None)])
    assert dict(models_from_log(lay)[STEM]) == {"riserva-1": 3}                        # conta l'accettata, non il tentativo
    probs = [i["problema"] for i in verify_course(c, check_audio=False, primary_notes="principale-1")["problemi"]]
    assert any("l'intestazione dice «principale-1» ma i log dicono «riserva-1»" in p for p in probs)
    assert any("1 lezioni scritte per più della metà da riserva-1" in p for p in probs)
    # intestazione veritiera e modello principale: nessun avviso
    (lay.appunti / f"{STEM}_appunti.md").write_text(frontmatter.join({**meta, "modello": "principale-1"}, "testo " * 600))
    _write_costs(lay, [(1, "principale-1", None), (2, "principale-1", None)])
    assert not [i for i in verify_course(c, check_audio=False, primary_notes="principale-1")["problemi"]
                if "intestazione" in i["problema"] or "scritte per più" in i["problema"]]


def test_verify_skips_lessons_with_blocks_missing_from_the_log(settings):
    from sbob.core.verify import models_from_log
    c = settings.corso("prova")
    lay = Layout.of(c)
    _write_costs(lay, [(1, "m", None), (3, "m", None)])                                 # il blocco 2 veniva dalla cache
    assert models_from_log(lay) == {}
