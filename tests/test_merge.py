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
    c.docente = "Rossi"
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "monolite"}))
    text = (lay.merge / "prova_appunti_monolite.md").read_text()
    assert rep.done == ["prova_appunti_monolite.md"] and "scarto" not in text
    assert text.startswith("# Corso di Prova — Appunti delle lezioni (2025-26, prof. Rossi)\n")
    assert "2 lezioni, dal 17/09/2025 al 24/09/2025." in text
    assert text.index("## Lezione 01 · 17/09/2025 · diodi, LED") < text.index("## Lezione 02 · 24/09/2025 · BJT")
    assert "Tipo: lezione · Edizione 2025-26 · File: 2025-09-17_prova_lez01_appunti.md" in text
    assert "### Titolo 2025-09-17_prova_lez01" in text and "title:" not in text.split("## Lezione 01")[1]   # frontmatter tolto


def test_compose_is_clean_and_deterministic(settings):
    c, lay = _course_with_notes(settings)
    files = merge.source_files(StepContext(settings, c, quiet=True), "appunti")[0]
    ctx = StepContext(settings, c, quiet=True)
    a, b = merge.build_monolite(merge.load_docs(files), ctx), merge.build_monolite(merge.load_docs(files), ctx)
    assert a == b                                                   # niente data di generazione: stesso hash
    for noise in ("ISTRUZIONI", "[[", "<br>", "━", "Ultimo aggiornamento", "Generato il"):
        assert noise not in a
    assert not any(ord(ch) > 0x2000 and ch not in "—·" for ch in a)  # niente emoji


def test_nest_headers_puts_top_level_under_the_section():
    assert merge.nest_headers("## A\n#### B\n```\n# c\n```") == "### A\n##### B\n```\n# c\n```"
    assert merge.nest_headers("niente titoli") == "niente titoli"


def test_heading_uses_first_title_when_no_topic(settings):
    c, lay = _course_with_notes(settings)
    (lay.appunti / "2025-09-30_prova_lez03_appunti.md").write_text(frontmatter.join(
        frontmatter.lesson_meta(c, "2025-09-30_prova_lez03"), "# Il MOSFET\n\ntesto"))
    docs = {d.stem: d for d in merge.load_docs(merge.source_files(StepContext(settings, c, quiet=True), "appunti")[0])}
    assert docs["2025-09-30_prova_lez03"].heading == "Lezione 03 · 30/09/2025 · Il MOSFET"


def test_split_limits_and_removes_stale(settings, monkeypatch):
    c, lay = _course_with_notes(settings)
    lay.ensure("merge")
    (lay.merge / "prova_appunti_9.md").write_text("vecchio")
    monkeypatch.setattr(merge, "WORD_LIMIT", 10)
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "split"}))
    assert rep.done == ["prova_appunti_1.md", "prova_appunti_2.md"] and not (lay.merge / "prova_appunti_9.md").exists()
    assert (lay.merge / "prova_appunti_1.md").read_text().startswith("# 2025-09-17_prova_lez01")


def test_tde_same_date_listed_separately(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("appunti")
    for name in ("TDE 2023-01-20 A_appunti.md", "TDE 2023-01-20 B_appunti.md", "lezione_appunti.md"):
        (lay.appunti / name).write_text("# Esercizio 1\ntesto")
    rep = merge.run(StepContext(settings, c, quiet=True, options={"modo": "tde"}))
    text = (lay.merge / "prova_tde.md").read_text()
    assert text.count("## Esame del 20/01/2023") == 2 and "lezione_appunti" not in text
    assert "2 temi d'esame" in text
    assert rep.done == ["prova_tde.md"]


def test_empty_and_bad_mode(settings):
    c = settings.corso("prova")
    assert merge.run(StepContext(settings, c, quiet=True)).notes
    assert merge.run(StepContext(settings, c, quiet=True, options={"modo": "boh"})).error
