from datetime import date

from sbob.core import frontmatter, naming
from sbob.core.batch import atomic_write_text, list_inputs, plan_jobs
from sbob.core.keys import load_keys
from sbob.core.layout import Layout
from sbob.core.report import Exit, StepReport
from sbob.core.status import course_status


def test_config_resolves_course_paths(settings, tmp_path):
    c = settings.corso("prova")
    assert c.cartella == tmp_path / "uni" / "1 anno " / "prova"   # lo spazio finale è preservato
    assert settings.modelli["refiner"]["provider"] == "gemini"      # default ereditato
    assert settings.modelli["notes"]["provider"] == "fake"          # override


def test_naming_roundtrip_and_legacy():
    n = naming.parse("2025-10-29_real_analysis_lez10_2")
    assert (n.data, n.slug, n.tipo, n.num, n.parte) == (date(2025, 10, 29), "real_analysis", "lez", 10, 2)
    assert n.stem == "2025-10-29_real_analysis_lez10_2"
    legacy = naming.parse("2025_09_17-real_analysis-ese01")
    assert legacy.stem == "2025-09-17_real_analysis_ese01"
    assert naming.parse("2025-09-17_x_lez03_appunti").num == 3
    assert naming.parse("Politecnico di Milano - Edificio 9") is None
    assert naming.parse_prd("2026-05-29 10-34").hour == 10


def test_next_numbers_continue_per_type():
    existing = [naming.parse("2025-09-17_c_lez01"), naming.parse("2025-09-18_c_lez02"),
                naming.parse("2025-09-19_c_ese01")]
    new = naming.next_numbers(existing, [date(2025, 9, 25), date(2025, 9, 24)], "c", "lez")
    assert [x.stem for x in new] == ["2025-09-24_c_lez03", "2025-09-25_c_lez04"]


def test_slugify():
    assert naming.slugify("Real and Functional Analysis") == "real_and_functional_analysis"
    assert naming.slugify(" Matematica  numerica ") == "matematica_numerica"


def test_keys(monkeypatch):
    monkeypatch.setenv("X_ACCOUNT1", "a")
    monkeypatch.setenv("X_ACCOUNT2", "b")
    monkeypatch.setenv("X_ACCOUNT4", "skipped")
    monkeypatch.setenv("X", "a")
    assert load_keys("X") == ["a", "b"]


def test_batch_skip_and_force(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for n in ("a.MP4", "b.mp4", ".hidden.mp4", "c.txt"):
        (src / n).write_text("x")
    inputs = list_inputs(src, [".mp4"])
    assert [p.name for p in inputs] == ["a.MP4", "b.mp4"]
    out = tmp_path / "out"
    atomic_write_text(out / "a.aac", "done")
    todo, done = plan_jobs(inputs, lambda p: out / f"{p.stem}.aac")
    assert [j.src.name for j in todo] == ["b.mp4"] and [j.src.name for j in done] == ["a.MP4"]
    todo, _ = plan_jobs(inputs, lambda p: out / f"{p.stem}.aac", force=True)
    assert len(todo) == 2


def test_frontmatter_roundtrip():
    text = frontmatter.join({"corso": "X", "argomenti": ["diodi", "BJT"], "vuoto": None}, "# Titolo\n\ncorpo")
    meta, body = frontmatter.split(text)
    assert meta == {"corso": "X", "argomenti": ["diodi", "BJT"]}
    assert body.startswith("# Titolo")
    assert frontmatter.split("niente frontmatter") == ({}, "niente frontmatter")


def test_report_exit_codes():
    r = StepReport("x")
    assert r.exit_code == Exit.OK
    r.fail("a", "boom")
    assert r.exit_code == Exit.ERROR
    r.done.append("b")
    assert r.exit_code == Exit.PARTIAL
    r.needs_human = "cookie scaduto"
    assert r.exit_code == Exit.HUMAN and r.to_dict()["exit_code"] == 3


def test_status_from_files(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("video", "audio", "trascrizioni", "appunti")
    s1, s2 = "2025-09-17_prova_lez01", "2025-09-18_prova_lez02"
    for p in (lay.video / f"{s1}.mp4", lay.audio / f"{s1}.aac", lay.trascrizioni / f"{s1}.md",
              lay.video / f"{s2}.mp4"):
        p.write_text("x")
    st = course_status(c)
    assert st["totali"] == {"lezioni": 2, "video": 2, "audio": 1, "trascrizione": 1, "appunti": 0}
    assert st["da_fare"] == {"audio": [s2], "trascrizione": [], "appunti": [s1]}
    assert [l["prossimo_passo"] for l in st["lezioni"]] == ["appunti", "audio"]
