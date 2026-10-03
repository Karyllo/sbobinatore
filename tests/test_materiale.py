import json
from pathlib import Path

import pytest

from sbob.core import frontmatter
from sbob.core.layout import Layout
from sbob.core.report import NeedsHuman
from sbob.core.status import course_status
from sbob.steps import materiale as mat
from sbob.steps.base import StepContext
from sbob.webeep.client import RemoteFile


class FakeClient:
    def __init__(self, files):
        self.files_, self.downloaded = files, []

    def files(self, course_id):
        return self.files_

    def download(self, f, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(f"contenuto {f.nome} v{f.modified}".encode())
        self.downloaded.append(f.relpath)


@pytest.fixture
def wcourse(settings, monkeypatch):
    c = settings.corso("prova")
    c.webeep_id = 99
    return c


def test_infer_tipo():
    assert mat.infer_tipo("Esami/Appello 2024-01-10.pdf") == "tde"
    assert mat.infer_tipo("Laboratorio/lab3.pdf") == "laboratorio"
    assert mat.infer_tipo("Esercizi/es05_soluzioni.pdf") == "esercitazione"
    assert mat.infer_tipo("Lezioni/Slide 01.pdf") == "slide"
    assert mat.infer_tipo("Materiali/laboratori/Lab03/labS03_2_Pricing.ipynb") == "laboratorio"       # plurale e numero
    assert mat.infer_tipo("Materiali/esempi di temi d'esame con soluzione/2022_02_03/esame_2022_02_03.pdf") == "tde"
    assert mat.infer_tipo("Materiali/esercitazioni/es01.pdf") == "esercitazione"
    assert mat.infer_tipo("Materiali/lezioni/elaborato_finale.pdf") == "slide"                           # "elaborato" non è lab


def test_text_conversion_fences_code(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("print('```')\n")
    out = mat._text_to_md(p)
    assert out.startswith("````python\n") and out.rstrip().endswith("````")      # fence più lungo di quello interno
    nb = tmp_path / "n.ipynb"
    nb.write_text(json.dumps({"cells": [{"cell_type": "markdown", "source": ["# T"]},
                                        {"cell_type": "code", "source": ["x=1"]}]}))
    assert mat._text_to_md(nb) == "# T\n\n```python\nx=1\n```"


def test_sync_incremental_and_never_deletes(wcourse, settings, monkeypatch):
    f1 = RemoteFile("Lezioni", "", "s1.txt", "u1", 10, 100)
    f2 = RemoteFile("Esami", "", "tde 2024.txt", "u2", 20, 100)
    client = FakeClient([f1, f2])
    monkeypatch.setattr("sbob.webeep.client.WebeepClient", lambda token: client)
    monkeypatch.setattr("sbob.auth.browser.load_token", lambda: "T")
    lay = Layout.of(wcourse)

    rep = mat.run(StepContext(settings, wcourse, quiet=True))
    assert sorted(client.downloaded) == ["Esami/tde 2024.txt", "Lezioni/s1.txt"]
    md = lay.materiale_md / "Esami" / "tde 2024.txt.md"
    meta, body = frontmatter.read(md)
    assert meta["tipo"] == "tde" and meta["conversione"] == "copia" and meta["fonte"] == "Esami/tde 2024.txt" \
        and "contenuto" in body

    client.downloaded.clear()                                          # secondo giro: nulla di nuovo
    rep = mat.run(StepContext(settings, wcourse, quiet=True))
    assert client.downloaded == [] and rep.done == []

    client.files_ = [RemoteFile("Lezioni", "", "s1.txt", "u1", 11, 200)]   # s1 modificato, tde sparito da WeBeep
    rep = mat.run(StepContext(settings, wcourse, quiet=True))
    assert client.downloaded == ["Lezioni/s1.txt"] and (lay.materiale / "Esami" / "tde 2024.txt").exists()
    assert any("non più su WeBeep" in n for n in rep.notes)
    assert "v200" in (lay.materiale_md / "Lezioni" / "s1.txt.md").read_text()     # riconvertito perché cambiato

    st = course_status(wcourse)["materiale"]
    assert st["file"] == 2 and st["convertiti"] == 2


def test_dry_run_downloads_nothing(wcourse, settings, monkeypatch):
    client = FakeClient([RemoteFile("L", "", "a.txt", "u", 1, 1)])
    monkeypatch.setattr("sbob.webeep.client.WebeepClient", lambda token: client)
    monkeypatch.setattr("sbob.auth.browser.load_token", lambda: "T")
    rep = mat.run(StepContext(settings, wcourse, dry_run=True, quiet=True))
    assert rep.done == ["L/a.txt"] and client.downloaded == [] and not Layout.of(wcourse).materiale.exists()


def test_missing_token_needs_login(wcourse, settings, monkeypatch):
    monkeypatch.setattr("sbob.auth.browser.load_token", lambda: None)
    with pytest.raises(NeedsHuman) as e:
        mat.run(StepContext(settings, wcourse, quiet=True))
    assert e.value.action == "sbob login"


def test_pdf_goes_through_convert_pdf_and_warns_on_degraded(settings, tmp_path, monkeypatch):
    c = settings.corso("prova")
    lay = Layout.of(c)
    (lay.materiale / "Slide").mkdir(parents=True)
    pdf = lay.materiale / "Slide" / "intro.pdf"
    pdf.write_bytes(b"%PDF fake")
    calls = []

    class R:
        complete, degraded, pages, failed, output = True, [2, 3], 3, {}, None

    def fake_convert(pdf_path, out, role, lingua, **kw):
        calls.append(kw["upgrade"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(frontmatter.join({**kw["meta"], "conversione": "misto"}, "testo\n"))
        return R

    class Reg:
        def __init__(self, s, t): pass
        def role(self, name, override=None): return object()

    monkeypatch.setattr(mat, "convert_pdf", fake_convert)
    monkeypatch.setattr(mat, "Registry", Reg)
    monkeypatch.setattr(mat, "text_fallback_role", lambda s, r: object())
    rep = mat.run(StepContext(settings, c, quiet=True))
    assert rep.done == ["Slide/intro.pdf.md"] and "2/3 pagine solo testo" in rep.warnings[0] and rep.exit_code == 2

    rep = mat.run(StepContext(settings, c, quiet=True))          # run dopo: le pagine solo-testo vengono riprovate
    assert len(calls) == 2 and calls == [True, True] and rep.warnings


def test_mappa_lists_materiale(settings, monkeypatch):
    from sbob.core import index
    c = settings.corso("prova")
    lay = Layout.of(c)
    (lay.materiale_md / "Esami").mkdir(parents=True)
    (lay.materiale / "Esami").mkdir(parents=True)
    (lay.materiale / "Esami" / "tde.pdf").write_bytes(b"x")
    (lay.materiale_md / "Esami" / "tde.pdf.md").write_text(frontmatter.join({"tipo": "tde", "conversione": "testo"}, "x"))
    r = index.render_course(c)
    idx = (lay.mappa / "INDICE.md").read_text()
    assert "## Materiale" in idx and "### Esami" in idx and "— tde *(solo testo)*" in idx and "originale" in idx
    assert r["materiale"][0]["tipo"] == "tde"


def test_set_course_field(tmp_path):
    from sbob.setup import set_course_field
    cfg = tmp_path / "sbob.toml"
    cfg.write_text('[corsi.a]\nnome = "A"\n\n[corsi.b]\nnome = "B"\n')
    set_course_field(cfg, "b", "webeep_id", 5)
    set_course_field(cfg, "b", "webeep_id", 6)                       # aggiornamento, non duplicato
    assert cfg.read_text() == '[corsi.a]\nnome = "A"\n\n[corsi.b]\nnome = "B"\nwebeep_id = 6\n'
    with pytest.raises(ValueError):
        set_course_field(cfg, "zzz", "x", 1)


SITE_HTML = '''<a href="slides/lez1.pdf">L1</a> <a href="/dispense/lez2.pptx">L2</a> <a href="https://drive.google.com/x.pdf">altro</a>
<a href="video.mp4">v</a> <a href="mailto:a@b.it">m</a> <a href="javascript:void(0)">j</a>'''


def test_site_links_same_host_only():
    links, other = mat.site_links(SITE_HTML, "https://prof.polimi.it/corso/index.html")
    assert links == ["https://prof.polimi.it/corso/slides/lez1.pdf", "https://prof.polimi.it/dispense/lez2.pptx"]
    assert other == 1                                                  # il link a Drive non si scarica


class SiteResp:
    def __init__(self, text="", status=200, body=b"", headers=None, url="https://prof.polimi.it/c/"):
        self.text, self.status_code, self._body, self.headers, self.url = text, status, body, headers or {}, url
    def raise_for_status(self): pass
    def iter_content(self, n): return iter([self._body])
    def __enter__(self): return self
    def __exit__(self, *a): return False


class SiteHttp:
    def __init__(self):
        self.files = {"https://prof.polimi.it/c/slides/lez1.pdf": (b"PDF1", {"ETag": '"v1"'})}
        self.requests = []

    def get(self, url, headers=None, stream=None, timeout=None):
        self.requests.append((url, dict(headers or {})))
        if url.endswith("/c/"):
            return SiteResp(text='<a href="slides/lez1.pdf">x</a>', url=url)
        body, h = self.files[url]
        if (headers or {}).get("If-None-Match") == h["ETag"]:
            return SiteResp(status=304)
        return SiteResp(body=body, headers=h)


def test_site_sync_incremental_with_etag(settings, monkeypatch):
    c = settings.corso("prova")
    c.extra["materiale_siti"] = ["https://prof.polimi.it/c/"]
    monkeypatch.setattr(mat, "_is_public_host", lambda h: True)
    http = SiteHttp()
    rep = StepReport = None
    from sbob.core.report import StepReport as SR
    rep = SR("materiale")
    mat.sync_siti(StepContext(settings, c, quiet=True), rep, http)
    dst = Layout.of(c).materiale / "Sito prof.polimi.it" / "c" / "slides" / "lez1.pdf"
    assert rep.done == ["Sito prof.polimi.it/c/slides/lez1.pdf"] and dst.read_bytes() == b"PDF1"

    rep2 = SR("materiale")
    mat.sync_siti(StepContext(settings, c, quiet=True), rep2, http)         # 2° giro: 304 Not Modified, niente da fare
    assert rep2.done == [] and rep2.skipped and http.requests[-1][1] == {"If-None-Match": '"v1"'}


def test_site_rejects_private_hosts(settings, monkeypatch):
    from sbob.core.report import StepReport as SR
    c = settings.corso("prova")
    c.extra["materiale_siti"] = ["http://192.168.1.10/materiale", "file:///etc/passwd"]
    rep = SR("materiale")
    mat.sync_siti(StepContext(settings, c, quiet=True), rep, SiteHttp())
    assert [f.item for f in rep.failed] == ["http://192.168.1.10/materiale", "file:///etc/passwd"]


def test_conversion_can_be_disabled(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    (lay.materiale / "A").mkdir(parents=True)
    (lay.materiale / "A" / "x.txt").write_text("ciao")
    rep = mat.run(StepContext(settings, c, quiet=True, options={"converti": False}))
    assert not lay.materiale_md.exists() and any("disattivata" in n for n in rep.notes)
    settings.raw["materiale"] = {"converti": False}                       # anche da config
    assert not mat.run(StepContext(settings, c, quiet=True)).done
    rep = mat.run(StepContext(settings, c, quiet=True, options={"converti": True}))   # --converti vince sulla config
    assert rep.done == ["A/x.txt.md"]


def test_cartella_option_converts_only_matching_paths(wcourse, settings, monkeypatch):
    client = FakeClient([RemoteFile("Lezioni", "", "s1.txt", "u1", 10, 100), RemoteFile("Esami", "", "tde 2024.txt", "u2", 20, 100)])
    monkeypatch.setattr("sbob.webeep.client.WebeepClient", lambda token: client)
    monkeypatch.setattr("sbob.auth.browser.load_token", lambda: "T")
    lay = Layout.of(wcourse)
    mat.run(StepContext(settings, wcourse, quiet=True, options={"cartella": "esami"}))      # maiuscole indifferenti
    assert (lay.materiale_md / "Esami" / "tde 2024.txt.md").exists() and not (lay.materiale_md / "Lezioni").exists()
    mat.run(StepContext(settings, wcourse, quiet=True))                                      # poi il resto
    assert (lay.materiale_md / "Lezioni" / "s1.txt.md").exists()


def test_404_files_are_taken_from_the_folder_zip(wcourse, settings, monkeypatch):
    import io
    import zipfile

    from sbob.core import secrets
    from sbob.webeep.client import MissingOnServer

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Marchese_limiti_continuit%C3%A0.pdf", b"dallo zip")
    zf = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    ok = RemoteFile("Materiali", "Esercizi", "bello.txt", "u0", 5, 100, 7, "bello.txt")
    odd = RemoteFile("Materiali", "Esercizi", "Marchese_limiti_continuit%C3%A0.pdf", "u1", 9, 100, 7,
                     "Marchese_limiti_continuit%C3%A0.pdf")
    gone = RemoteFile("Materiali", "Esercizi", "rotto.pdf", "u2", 9, 100, 7, "rotto.pdf")

    class Client(FakeClient):
        zips = []
        def download(self, f, dst):
            if f is not ok:
                raise MissingOnServer("404")
            super().download(f, dst)
        def folder_zip(self, module, session):
            Client.zips.append((module, bool(session)))
            return zf
    client = Client([ok, odd, gone])
    monkeypatch.setattr("sbob.webeep.client.WebeepClient", lambda token: client)
    monkeypatch.setattr("sbob.auth.browser.load_token", lambda: "T")
    monkeypatch.setattr(secrets, "load_cookie", lambda name: "sessione")
    lay = Layout.of(wcourse)
    rep = mat.run(StepContext(settings, wcourse, quiet=True, options={"converti": False}))
    assert (lay.materiale / "Materiali" / "Esercizi" / odd.nome).read_bytes() == b"dallo zip"
    assert Client.zips == [(7, True)]                                        # una sola richiesta per cartella
    assert any("rotto.pdf" in n and "nemmeno nello zip" in n for n in rep.notes) and not rep.failed
    assert sorted(rep.done) == sorted([ok.relpath, odd.relpath])


def test_results_lists_are_never_converted():
    for name in ("Esiti AM1 - 03-07-26 - IV appello.pdf", "Esito AM1 - 09-06-26.pdf", "Risultati prova.pdf", "graduatoria.pdf"):
        assert mat.is_results_list(name), name
    for name in ("Testo AM1 - I appello.pdf", "Traccia soluzioni AM1.pdf", "Derivata.pdf", "Sviluppi di Taylor.pdf"):
        assert not mat.is_results_list(name), name
