import json

import pytest
from typer.testing import CliRunner

from sbob import notebooklm_cli as nlm
from sbob import scelta
from sbob.cli import app
from sbob.core import frontmatter
from sbob.core.layout import Layout
from sbob.core.report import NeedsHuman
from sbob.steps import notebook
from sbob.steps.base import StepContext

runner = CliRunner()


class FakeNLM:
    """Finto CLI notebooklm: tiene i taccuini in memoria e registra le chiamate."""

    def __init__(self):
        self.notebooks: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.n = 0
        self.fail_wait = False
        self.auth_error = False

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def __call__(self, *args):
        self.calls.append(args)
        if self.auth_error:
            return "", "Error: please login", 1
        cmd = args[0]
        if cmd == "create":
            nb = self._id("nb")
            self.notebooks[nb] = {"title": args[1], "sources": {}, "persona": None}
            return json.dumps({"notebook": {"id": nb, "title": args[1]}}), "", 0
        nbid = args[args.index("-n") + 1]
        book = self.notebooks[nbid]
        if cmd == "configure":
            book["persona"] = args[args.index("--persona") + 1]
            return json.dumps({"configured": True}), "", 0
        sub = args[1]
        if sub == "list":
            return json.dumps({"sources": [{"id": i, "title": t, "status": "ready"} for i, t in book["sources"].items()]}), "", 0
        if sub == "add":
            sid = self._id("src")
            from pathlib import Path
            book["sources"][sid] = Path(args[2]).name
            book.setdefault("texts", {})[sid] = Path(args[2]).read_text()
            return json.dumps({"source": {"id": sid}}), "", 0
        if sub == "wait":
            if self.fail_wait:
                return json.dumps({"error": True, "message": "processing failed"}), "", 1
            return json.dumps({"status": "ready"}), "", 0
        if sub == "delete":
            book["sources"].pop(args[2], None)
            return json.dumps({"success": True}), "", 0
        raise AssertionError(args)

    def only(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


@pytest.fixture
def nlm_fake(monkeypatch):
    fake = FakeNLM()
    monkeypatch.setattr(nlm, "nb", fake)
    return fake


def _course(settings):
    c = settings.corso("prova")
    c.inizio_corso = "2025-09-15"
    lay = Layout.of(c)
    lay.ensure("appunti", "materiale_md")
    for stem, topic in (("2025-09-17_prova_lez01", "diodi"), ("2025-09-24_prova_lez02", "BJT")):
        (lay.appunti / f"{stem}_appunti.md").write_text(frontmatter.join(frontmatter.lesson_meta(c, stem, argomento=topic), "# T\n\ncorpo"))
    for rel in ("Materiali/esempi di temi d'esame/TDE 2023-01-20.md", "Materiali/esempi di temi d'esame/TDE 2024-02-10.md",
                "Materiali/slide.md", "Esercitazioni/Es1/uno.md", "Esercitazioni/Es1/sub/due.md", "libero.md"):
        p = lay.materiale_md / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {p.stem}\n\ntesto")
    return c, lay


def _run(settings, c, **kw):
    opts = kw.pop("options", {"esplicito": True})
    return notebook.run(StepContext(settings, c, quiet=True, options=opts, **kw))


def test_plan_sources_groups_by_first_level_under_section(settings):
    c, lay = _course(settings)
    titles = set(notebook.plan_sources(c))
    assert titles == {"Appunti", "Materiali — esempi di temi d'esame", "Materiali", "Esercitazioni — Es1", "Materiale"}
    tde = notebook.plan_sources(c)["Materiali — esempi di temi d'esame"]
    assert tde.index("Esame del 20/01/2023") < tde.index("Esame del 10/02/2024")
    assert "trascrizioni" not in " ".join(titles).lower()


def test_plan_sources_split_notes_over_limit(settings, monkeypatch):
    from sbob.steps import merge
    c, lay = _course(settings)
    monkeypatch.setattr(merge, "WORD_LIMIT", 3)
    assert {"Appunti (1)", "Appunti (2)"} <= set(notebook.plan_sources(c))


def test_disabled_by_default_unless_explicit(settings, nlm_fake):
    c, _ = _course(settings)
    rep = _run(settings, c, options={})
    assert rep.notes and not rep.done and not nlm_fake.calls


def test_first_run_creates_notebook_sources_and_persona_then_second_run_is_noop(settings, nlm_fake):
    c, lay = _course(settings)
    rep = _run(settings, c)
    assert not rep.error and not rep.failed
    (nbid, book), = nlm_fake.notebooks.items()
    assert book["title"] == "Corso di Prova (2025-26)" and "cita sempre" in book["persona"].lower()
    assert sorted(book["sources"].values()) == sorted(f"{t}.md" for t in notebook.plan_sources(c))
    assert ctx_state(settings, c)["id"] == nbid
    rep2 = _run(settings, c)
    assert not rep2.done and len(rep2.skipped) == 5
    assert len(nlm_fake.only("source", "add")) == 5 and len(nlm_fake.only("configure")) == 1   # il 2° run non carica né riconfigura


def ctx_state(settings, c):
    return StepContext(settings, c, quiet=True).manifest().notebook


def test_changed_source_is_added_waited_then_old_deleted_in_order(settings, nlm_fake):
    c, lay = _course(settings)
    _run(settings, c)
    old_id = ctx_state(settings, c)["sources"]["Appunti"]["id"]
    (lay.appunti / "2025-09-24_prova_lez02_appunti.md").write_text(frontmatter.join({"lezione": "2025-09-24_prova_lez02"}, "# T\n\nmodificato"))
    nlm_fake.calls.clear()
    rep = _run(settings, c)
    assert rep.done == ["sostituita: Appunti"] and len(rep.skipped) == 4
    kinds = [(a[0], a[1]) if a[0] == "source" else (a[0],) for a in nlm_fake.calls if a[:2] != ("source", "list")]
    assert kinds == [("source", "add"), ("source", "wait"), ("source", "delete")]
    assert nlm_fake.only("source", "delete")[0][2] == old_id
    new_id = ctx_state(settings, c)["sources"]["Appunti"]["id"]
    assert new_id != old_id and "modificato" in nlm_fake.notebooks[ctx_state(settings, c)["id"]]["texts"][new_id]


def test_failed_processing_keeps_old_source(settings, nlm_fake):
    c, lay = _course(settings)
    _run(settings, c)
    old = dict(ctx_state(settings, c)["sources"]["Appunti"])
    (lay.appunti / "2025-09-24_prova_lez02_appunti.md").write_text(frontmatter.join({}, "# T\n\nnuovo"))
    nlm_fake.fail_wait = True
    rep = _run(settings, c)
    assert len(rep.failed) == 1 and "Appunti" in str(rep.failed)
    assert ctx_state(settings, c)["sources"]["Appunti"] == old
    book = nlm_fake.notebooks[ctx_state(settings, c)["id"]]
    assert old["id"] in book["sources"] and len(book["sources"]) == 5          # la nuova è stata tolta


def test_removed_local_source_is_deleted_but_manual_ones_untouched(settings, nlm_fake):
    c, lay = _course(settings)
    _run(settings, c)
    book = nlm_fake.notebooks[ctx_state(settings, c)["id"]]
    book["sources"]["manuale"] = "mio.pdf"                                        # aggiunta a mano dall'utente
    (lay.materiale_md / "libero.md").unlink()
    rep = _run(settings, c)
    assert "rimossa: Materiale" in rep.done
    assert "manuale" in book["sources"] and "Materiale" not in ctx_state(settings, c)["sources"]


def test_source_limit_warns(settings, nlm_fake):
    c, _ = _course(settings)
    settings.raw["notebook"] = {"max_sorgenti": 2}
    rep = _run(settings, c)
    assert len(rep.warnings) == 3 and len(rep.done) >= 3                          # taccuino + persona + 2 sorgenti
    assert len(ctx_state(settings, c)["sources"]) == 2


def test_dry_run_changes_nothing(settings, nlm_fake):
    c, _ = _course(settings)
    rep = _run(settings, c, dry_run=True)
    assert "creerei il taccuino" in rep.done and not nlm_fake.calls
    assert not ctx_state(settings, c)


def test_auth_error_needs_human(settings, nlm_fake):
    c, _ = _course(settings)
    nlm_fake.auth_error = True
    with pytest.raises(NeedsHuman) as e:
        _run(settings, c)
    assert e.value.action == "notebooklm login"


def test_archive_sources_only_on_command(settings, nlm_fake):
    c, lay = _course(settings)
    c.archivio = {"2024-25": 18181}
    _run(settings, c)
    assert not any(t.startswith("Edizione") for t in ctx_state(settings, c)["sources"])
    arch = Layout.of(notebook.archive_course(c, "2024-25"))
    arch.ensure("appunti")
    (arch.appunti / "2024-09-18_prova_lez01_appunti.md").write_text(frontmatter.join({}, "# Vecchia\n\ntesto"))
    rep = _run(settings, c, options={"azione": "aggiungi-archivio", "anno": "2024-25"})
    assert "aggiunta: Edizione 2024-25 — Appunti" in rep.done
    assert ctx_state(settings, c)["archivi"] == ["2024-25"]
    assert not _run(settings, c).done                                              # i run successivi le tengono aggiornate
    rep = _run(settings, c, options={"azione": "rimuovi-archivio", "anno": "2024-25"})
    assert rep.done == ["rimossa: Edizione 2024-25 — Appunti"]
    assert _run(settings, c, options={"azione": "aggiungi-archivio", "anno": "2000-01"}).error


# ---- scelta dei corsi -------------------------------------------------------

WB = [{"id": 1, "nome": "078046 - ANALISI MATEMATICA (2) (ROSSI MARIO) [2025-26]", "anno": "2025-26"},
      {"id": 2, "nome": "055003 - FISICA (BIANCHI ANNA) [2025-26]", "anno": "2025-26"},
      {"id": 3, "nome": "078046 - ANALISI MATEMATICA (2) (ROSSI MARIO) [2024-25]", "anno": "2024-25"}]


def test_short_name():
    assert scelta.short_name(WB[0]["nome"]) == "Analisi Matematica"


def test_scegli_with_ids_adds_blocks_and_never_removes(settings, monkeypatch):
    monkeypatch.setattr(scelta, "webeep_courses", lambda: WB)
    rep = scelta.run_scegli(settings, [1, 2], False, True)
    assert rep.done == ["analisi_matematica ← WeBeep 1", "fisica ← WeBeep 2"]
    from sbob.config import load_settings
    s2 = load_settings()
    c = s2.corso("analisi_matematica")
    assert c.webeep_id == 1 and c.anno_accademico == "2025-26" and c.docente == "Rossi Mario"
    assert c.sorgenti[0] == {"tipo": "archivio"} and "2025-26/analisi_matematica" in str(c.cartella)
    assert s2.corso("prova").nome == "Corso di Prova"                              # il resto è intatto
    assert scelta.run_scegli(s2, [1], False, True).done == []                       # già collegato: niente doppioni
    assert scelta.run_scegli(s2, [999], False, True).error


def test_scegli_without_terminal_needs_ids(settings, monkeypatch):
    monkeypatch.setattr(scelta, "webeep_courses", lambda: WB)
    assert "--id" in scelta.run_scegli(settings, None, False, True).error


def test_scegli_unlink_keeps_files(settings, monkeypatch):
    from sbob.config import load_settings
    monkeypatch.setattr(scelta, "webeep_courses", lambda: WB)
    scelta.run_scegli(settings, [1, 2], False, True)
    s2 = load_settings()
    import questionary
    # selezione simulata: solo il corso 1 resta spuntato
    class Q:
        def __init__(self, v): self.v = v
        def ask(self): return self.v
    monkeypatch.setattr(scelta, "interactive", lambda as_json: True)
    monkeypatch.setattr(questionary, "checkbox", lambda *a, **k: Q([1]))
    monkeypatch.setattr(questionary, "confirm", lambda *a, **k: Q(True))
    rep = scelta.run_scegli(s2, None, False, False)
    assert rep.done == ["fisica scollegato (i file restano)"]
    assert load_settings().corso("fisica").webeep_id is None


def test_aggiorna_runs_only_linked_courses(settings, monkeypatch):
    from sbob import cli
    calls = []
    monkeypatch.setattr(cli, "_run_chain", lambda corso, steps, **kw: calls.append(corso) or [])
    s = settings
    s.corsi["prova"].webeep_id = None
    r = runner.invoke(app, ["aggiorna", "--json"])
    assert "Nessun corso collegato" in r.stdout and not calls
    from sbob.config import Course
    s.corsi["prova"].webeep_id = 5
    monkeypatch.setattr(cli, "_settings", lambda: s)
    from sbob.core.report import StepReport
    monkeypatch.setattr(cli, "_run_chain", lambda corso, steps, **kw: calls.append(corso) or [StepReport(step="x", corso=corso)])
    runner.invoke(app, ["aggiorna", "--json"])
    assert calls == ["prova"]


def test_map_source_is_clean_deterministic_and_updates_with_schede(settings, nlm_fake):
    from sbob.core import index
    c, lay = _course(settings)
    schede = {"2025-09-17_prova_lez01": {"riassunto": "Diodi e giunzioni.", "hash": "h1",
                                         "concetti": [{"nome": "Giunzione pn", "ruolo": "introdotto"}], "prerequisiti": ["Semiconduttore"]},
              "2025-09-24_prova_lez02": {"riassunto": "BJT.", "hash": "h2",
                                         "concetti": [{"nome": "Giunzione pn", "ruolo": "ripreso"}, {"nome": "BJT", "ruolo": "introdotto"}],
                                         "prerequisiti": []}}
    index.save_schede(c, schede)
    text = notebook.plan_sources(c)["Mappa"]
    assert text == notebook.plan_sources(c)["Mappa"]                                   # deterministico
    assert "- Giunzione pn: introdotto in Lezione 01 · 17/09/2025; ripreso in Lezione 02 · 24/09/2025" in text
    assert "Concetti: Giunzione pn (nuovo)" in text and "Prerequisiti: Semiconduttore" in text
    assert "](<" not in text and "../" not in text                                      # niente link a file locali
    _run(settings, c)
    assert "Mappa" in ctx_state(settings, c)["sources"]
    schede["2025-09-24_prova_lez02"]["riassunto"] = "BJT e polarizzazione."
    index.save_schede(c, schede)
    rep = _run(settings, c)
    assert rep.done == ["sostituita: Mappa"]                                            # cambia la scheda → solo la Mappa si ricarica
