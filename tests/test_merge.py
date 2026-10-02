from sbob.core import frontmatter
from sbob.core.layout import Layout
from sbob.steps import merge
from sbob.steps.base import StepContext


def test_downgrade_headers_skips_code_blocks():
    src = "# Titolo\ntesto\n```python\n# commento\nx = 1\n```\n## Sotto\n~~~\n# ancora codice\n~~~\n#hashtag\n####### no"
    out = merge.downgrade_headers(src).split("\n")
    assert out[0] == "## Titolo" and out[3] == "# commento" and out[6] == "### Sotto"
    assert out[8] == "# ancora codice" and out[10] == "#hashtag"


def test_week_and_dates():
    assert merge.week_of("2025-09-15", "2025-09-15") == 1
    assert merge.week_of("2025-09-22", "2025-09-15") == 2
    assert merge.week_of("2025-09-01", "2025-09-15") == 0
    assert merge.week_of("2025-09-01", None) == "?"
    assert merge.extract_date("Appello 20-01-2023.md") == "2023-01-20"
    assert merge.extract_date("senza data.md") is None


def _course_with_notes(settings):
    c = settings.corso("prova")
    c.inizio_corso = "2025-09-15"
    lay = Layout.of(c)
    lay.ensure("appunti")
    for stem, topics in (("2025-09-24_prova_lez02", ["BJT"]), ("2025-09-17_prova_lez01", ["diodi", "LED"])):
        meta = frontmatter.lesson_meta(c, stem, argomenti=topics)
        (lay.appunti / f"{stem}_appunti.md").write_text(frontmatter.join(meta, f"# Titolo {stem}\n\ncorpo"))
    (lay.appunti / "2025-09-17_prova_lez01.parziale.md").write_text("scarto")
    return c, lay


def test_monolite(settings):
    c, lay = _course_with_notes(settings)
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "monolite"}))
    text = (lay.merge / "prova_appunti_monolite.md").read_text()
    assert rep.done == ["prova_appunti_monolite.md"] and "scarto" not in text
    assert text.index("[[ID_SESSIONE_1]]") < text.index("[[ID_SESSIONE_2]]")          # ordine cronologico
    assert "Lezione 01 — diodi, LED" in text and "(Settimana 1)" in text and "(Settimana 2)" in text
    assert "## Titolo 2025-09-17_prova_lez01" in text and "title:" not in text.split("INDICE")[1]  # frontmatter tolto


def test_split_limits_and_removes_stale(settings, monkeypatch):
    c, lay = _course_with_notes(settings)
    lay.ensure("merge")
    (lay.merge / "prova_appunti_9.md").write_text("vecchio")
    monkeypatch.setattr(merge, "WORD_LIMIT", 10)
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "split"}))
    assert rep.done == ["prova_appunti_1.md", "prova_appunti_2.md"] and not (lay.merge / "prova_appunti_9.md").exists()
    assert (lay.merge / "prova_appunti_1.md").read_text().startswith("# 2025-09-17_prova_lez01")


def test_tde_unique_ids_for_same_date(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("appunti")
    for name in ("TDE 2023-01-20 A_appunti.md", "TDE 2023-01-20 B_appunti.md", "lezione_appunti.md"):
        (lay.appunti / name).write_text("# Esercizio 1\ntesto")
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "tde"}))
    text = (lay.merge / "prova_tde.md").read_text()
    assert "[[ID_TDE_2023_01_20]]" in text and "[[ID_TDE_2023_01_20_2]]" in text and "lezione" not in text.lower().split("indice")[1].split("---")[0]
    assert rep.done == ["prova_tde.md"]


def test_empty_and_bad_mode(settings):
    c = settings.corso("prova")
    assert merge.run(StepContext(settings, c, quiet=True)).notes
    assert merge.run(StepContext(settings, c, quiet=True, options={"modo": "boh"})).error
