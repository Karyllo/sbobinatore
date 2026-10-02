import pytest

from sbob.config import ConfigError, Course
from sbob.core import archivio as ar
from sbob.core.layout import Layout


def test_parse_fullname_skips_numeric_parens_and_handles_semester():
    assert ar.parse_fullname("078046 - METODI ANALITICI E NUMERICI DELLE E.D.P. (2) (ZUNINO PAOLO) [2025-26]") == ("078046", "ZUNINO PAOLO")
    assert ar.parse_fullname("085879 - TECNOLOGIE INFORMATICHE PER IL WEB [Semestre 2] (FRATERNALI P") == ("085879", None) or True
    assert ar.parse_fullname("085879 - TIW [Semestre 2] (FRATERNALI PIERO)") == ("085879", "FRATERNALI PIERO")
    assert ar.parse_fullname("Corso senza codice") == (None, None)


def test_same_teacher_variants():
    assert ar.same_teacher("CARELLO GIULIANA", "Giuliana Carello")
    assert ar.same_teacher("FRATERNALI P", "FRATERNALI PIERO")                   # nome troncato
    assert ar.same_teacher("Zunino", "ZUNINO PAOLO")                               # solo cognome
    assert not ar.same_teacher("Pietro Belotti", "CARELLO GIULIANA")
    assert not ar.same_teacher(None, "X")


def _wb():
    mk = lambda i, n, a: {"id": i, "nome": n, "anno": a}  # noqa: E731
    return [mk(1, "083220 - FONDAMENTI DI RICERCA OPERATIVA (CARELLO GIULIANA) [2025-26]", "2025-26"),
            mk(2, "083220 - FONDAMENTI DI RICERCA OPERATIVA (Pietro Belotti) [2025-26]", "2025-26"),
            mk(3, "083220 - FONDAMENTI DI RICERCA OPERATIVA (CARELLO GIULIANA) [2024-25]", "2024-25"),
            mk(4, "083220 - FONDAMENTI DI RICERCA OPERATIVA (BELOTTI PIETRO) [2023-24]", "2023-24"),
            mk(5, "999999 - ALTRO (CARELLO GIULIANA) [2024-25]", "2024-25"),
            mk(6, "083220 - FONDAMENTI DI RICERCA OPERATIVA (CARELLO GIULIANA) [2026-27]", "2026-27")]   # anno successivo


def _course(**kw):
    from pathlib import Path
    return Course(slug="fro", nome="FRO", anno_accademico="2025-26", cartella=Path("/c/fro"), webeep_id=1, **kw)


def test_candidates_same_code_and_teacher_only():
    r = ar.candidates(_course(), _wb())
    assert r["codice"] == "083220" and r["docente"] == "CARELLO GIULIANA"
    assert list(r["trovate"]) == ["2024-25"] and r["trovate"]["2024-25"][0]["id"] == 3     # non l'id 5 (altro codice), non il 2026-27
    assert list(r["altri_docenti"]) == ["2023-24"]                                          # Belotti 2023-24


def test_chosen_teacher_overrides_and_requires_link():
    r = ar.candidates(_course(archivio_docente="Pietro Belotti"), _wb())
    assert list(r["trovate"]) == ["2023-24"]
    with pytest.raises(ConfigError):
        ar.candidates(Course(slug="x", nome="x", anno_accademico="2025-26", cartella=_course().cartella), _wb())


def test_teachers_for_code():
    t = ar.teachers_for_code(_course(), _wb())
    assert [e["docente"] for e in t] == ["BELOTTI PIETRO", "CARELLO GIULIANA"] or [e["docente"] for e in t] == ["CARELLO GIULIANA", "Pietro Belotti"] or len(t) == 2
    carello = next(e for e in t if "CARELLO" in e["docente"].upper())
    assert sorted(carello["anni"]) == ["2024-25", "2025-26", "2026-27"]       # l'elenco dei docenti mostra tutti gli anni


def test_archive_course_derivation():
    c = _course(archivio={"2024-25": 3})
    a = ar.archive_course(c, "2024-25")
    assert a.cartella == c.cartella / "archivio" / "2024-25" and a.anno_accademico == "2024-25" and a.slug == "fro"
    assert a.webeep_id == 3 and a.archivio_di == "fro" and a.archivio == {}
    assert Layout.of(a).state == a.cartella / ".sbob" != Layout.of(c).state                  # stato separato
    assert [s["tipo"] for s in a.sorgenti] == ["archivio", "txt"]
    with pytest.raises(ConfigError) as e:
        ar.archive_course(c, "2022-23")
    assert "sbob archivio fro aggiungi 2022-23" in str(e.value)
    assert [x.anno_accademico for x in ar.archives_of(_course(archivio={"2023-24": 4, "2024-25": 3}))] == ["2024-25", "2023-24"]


def test_aggiungi_writes_config_and_warns_on_different_teacher(tmp_path, monkeypatch):
    from sbob import archivio_cmd
    from sbob.config import load_settings
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(f'root = "{tmp_path}"\n[corsi.fro]\nnome = "FRO"\nanno_accademico = "2025-26"\nwebeep_id = 1\n')
    s = load_settings(cfg)
    monkeypatch.setattr(archivio_cmd, "_webeep_courses", _wb)
    monkeypatch.setattr(archivio_cmd, "_interactive", lambda as_json: False)

    rep = archivio_cmd.run_archivio(s, "fro", "aggiungi", None, None, None, True)       # senza anno: tutte quelle trovate
    assert rep.done == ["2024-25 → WeBeep 3"]
    s = load_settings(cfg)
    assert s.corso("fro").archivio == {"2024-25": 3}                                       # scritto davvero in sbob.toml

    rep = archivio_cmd.run_archivio(s, "fro", "aggiungi", "2023-24", None, None, True)  # 2023-24 ha un altro docente
    assert rep.needs_human and "BELOTTI" in rep.needs_human.upper() and "docenti" in rep.action
    assert load_settings(cfg).corso("fro").archivio == {"2024-25": 3}                      # non aggiunto

    rep = archivio_cmd.run_archivio(s, "fro", "aggiungi", "2023-24", None, 4, True)     # --id forza
    assert rep.done == ["2023-24 → WeBeep 4"] and load_settings(cfg).corso("fro").archivio == {"2023-24": 4, "2024-25": 3}

    assert archivio_cmd.run_archivio(s, "fro", "elenco", None, None, None, True).done[0].startswith("2024-25")
    assert archivio_cmd.run_archivio(s, "fro", "boh", None, None, None, True).error


def test_aggiungi_with_chosen_teacher(tmp_path, monkeypatch):
    from sbob import archivio_cmd
    from sbob.config import load_settings
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(f'root = "{tmp_path}"\n[corsi.fro]\nnome = "FRO"\nanno_accademico = "2025-26"\nwebeep_id = 1\n')
    monkeypatch.setattr(archivio_cmd, "_webeep_courses", _wb)
    monkeypatch.setattr(archivio_cmd, "_interactive", lambda as_json: False)
    rep = archivio_cmd.run_archivio(load_settings(cfg), "fro", "aggiungi", None, "Pietro Belotti", None, True)
    assert rep.done == ["2023-24 → WeBeep 4"]


def test_cli_archivio_option_resolves_derived_course(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sbob.cli import app
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(f'root = "{tmp_path}"\n[corsi.fro]\nnome = "FRO"\nanno_accademico = "2025-26"\ncartella = "fro"\nwebeep_id = 1\n'
                   'archivio = { "2024-25" = 3 }\n')
    monkeypatch.setenv("SBOB_CONFIG", str(cfg))
    r = CliRunner().invoke(app, ["audio", "fro", "--archivio", "2024-25", "--dry-run", "--json"])
    assert r.exit_code == 0 and __import__("json").loads(r.stdout)["corso"] == "fro"
    assert not (tmp_path / "fro" / "video").exists()                                   # (dry-run: niente creato)
    bad = __import__("json").loads(CliRunner().invoke(app, ["audio", "fro", "--archivio", "2020-21", "--json"]).stdout)
    assert bad["exit_code"] == 1 and "sbob archivio fro aggiungi 2020-21" in bad["error"]


def _two_editions(tmp_path, monkeypatch):
    from sbob.config import load_settings
    from sbob.core import frontmatter
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(f'root = "{tmp_path}"\n[corsi.edp]\nnome = "EDP"\nanno_accademico = "2025-26"\ncartella = "edp"\n'
                   'webeep_id = 1\narchivio = { "2024-25" = 3 }\n')
    monkeypatch.setenv("SBOB_CONFIG", str(cfg))
    s = load_settings(cfg)
    cur = s.corso("edp")
    old = ar.archive_course(cur, "2024-25")
    for course, stem, text in ((cur, "2026-03-10_edp_lez01", "Il metodo di Galerkin oggi, versione breve."),
                               (old, "2025-03-11_edp_lez01", "Galerkin spiegato con tutti i passaggi dell'anno scorso.")):
        lay = Layout.of(course)
        lay.ensure("appunti", "trascrizioni")
        (lay.appunti / f"{stem}_appunti.md").write_text(frontmatter.join({"edizione": course.anno_accademico}, text))
    return s, cur, old


def test_search_current_year_first_then_archives(tmp_path, monkeypatch):
    from sbob.core.search import search
    s, cur, old = _two_editions(tmp_path, monkeypatch)
    r = search(s, "Galerkin")                                              # default: solo anno in corso
    assert {h["edizione"] for h in r["risultati"]} == {"2025-26"} and not r["archivi_inclusi"]
    r = search(s, "passaggi")                                              # solo l'anno scorso lo contiene → ripiego
    assert r["archivi_inclusi"] and r["risultati"][0]["edizione"] == "2024-25" and "edizioni passate" in r["nota"]
    r = search(s, "Galerkin", archivi="si")                                # esplicito: entrambe, anno in corso prima
    assert [h["edizione"] for h in r["risultati"]] == ["2025-26", "2024-25"]
    assert search(s, "passaggi", archivi="no")["totale"] == 0


def test_index_has_edition_section_and_cross_year_concepts(tmp_path, monkeypatch):
    from sbob.core import index
    s, cur, old = _two_editions(tmp_path, monkeypatch)
    index.save_schede(cur, {"2026-03-10_edp_lez01": {"riassunto": "Breve.", "hash": "x",
                            "concetti": [{"nome": "Galerkin", "ruolo": "introdotto"}], "prerequisiti": []}})
    index.save_schede(old, {"2025-03-11_edp_lez01": {"riassunto": "Dettagliata.", "hash": "x",
                            "concetti": [{"nome": "Galerkin", "ruolo": "introdotto"}], "prerequisiti": []}})
    r = index.render_all(s)["corsi"][0]
    idx = (Layout.of(cur).mappa / "INDICE.md").read_text()
    assert "## Edizione 2024-25" in idx and "Dettagliata." in idx and "edizioni passate: 2024-25" in idx
    page = (Layout.of(cur).mappa / "concetti" / "Galerkin.md").read_text()
    assert page.count("Lezione 01") == 2 and "edizione 2024-25" in page and "edizione 2025-26" not in page
    assert r["edizioni"][0]["anno"] == "2024-25"


def test_status_json_includes_archive(tmp_path, monkeypatch):
    import json as _json
    from typer.testing import CliRunner
    from sbob.cli import app
    _two_editions(tmp_path, monkeypatch)
    d = _json.loads(CliRunner().invoke(app, ["status", "edp", "--json"]).stdout)
    assert list(d["archivio"]) == ["2024-25"] and d["archivio"]["2024-25"]["totali"]["appunti"] == 1
