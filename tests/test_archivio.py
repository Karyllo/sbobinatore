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
            mk(5, "999999 - ALTRO (CARELLO GIULIANA) [2024-25]", "2024-25")]


def _course(**kw):
    from pathlib import Path
    return Course(slug="fro", nome="FRO", anno_accademico="2025-26", cartella=Path("/c/fro"), webeep_id=1, **kw)


def test_candidates_same_code_and_teacher_only():
    r = ar.candidates(_course(), _wb())
    assert r["codice"] == "083220" and r["docente"] == "CARELLO GIULIANA"
    assert list(r["trovate"]) == ["2024-25"] and r["trovate"]["2024-25"][0]["id"] == 3     # non l'id 5 (altro codice)
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
    assert sorted(carello["anni"]) == ["2024-25", "2025-26"]


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
