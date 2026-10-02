from datetime import datetime

import pytest

from sbob import webex
from sbob.core.layout import Layout
from sbob.core.report import NeedsHuman
from sbob.steps import download
from sbob.steps.base import StepContext

ID1, ID2, ID3 = "a" * 32, "b" * 32, "c" * 32
URL = "https://politecnicomilano.webex.com/recordingservice/sites/politecnicomilano/recording/{}/playback"


class FakeWebex:
    """Webex finto: `created` = id → "YYYY-MM-DD HH:MM:SS" (data che darebbe l'API); registra le chiamate."""

    def __init__(self, monkeypatch, created, expired_first=0):
        self.created, self.info_calls, self.downloaded = created, [], []
        self.expired = expired_first
        monkeypatch.setattr(download, "_ticket_session", lambda ctx, renew=False: self.session(renew))
        monkeypatch.setattr(download.webex, "recordings", self.recordings)
        monkeypatch.setattr(download.webex, "download", self.download)
        self.renewed = 0

    def session(self, renew):
        self.renewed += renew
        return object()

    def recordings(self, ids, http, workers=8):
        if self.expired:
            self.expired -= 1
            raise webex.TicketError("scaduto")
        ids = list(ids)
        self.info_calls.append(ids)
        return {v: webex.Recording(id=v, name="Stanza personale", created=datetime.strptime(self.created[v], "%Y-%m-%d %H:%M:%S"),
                                   created_gmt=None, url=f"https://cdn/{v}.mp4",
                                   audio_url=f"https://cdn/{v}.mp3") for v in ids}

    def download(self, jobs, dest, log=print, connections=16):
        dest.mkdir(parents=True, exist_ok=True)
        out = {}
        for j in jobs:
            (dest / j.name).write_text("file")
            out[j.key] = dest / j.name
            self.downloaded.append(j.key)
        return out


def links(course, *ids, extra=""):
    (course.cartella / "link.txt").write_text("".join(URL.format(i) + "\n" for i in ids) + extra)


@pytest.fixture
def course_with_links(settings):
    c = settings.corso("prova")
    c.cartella.mkdir(parents=True)
    (c.cartella / "link.txt").write_text("")
    return c


def test_download_numbers_and_is_incremental(settings, course_with_links, monkeypatch):
    c = course_with_links
    fake = FakeWebex(monkeypatch, {ID2: "2025-09-18 10:00:00", ID1: "2025-09-17 09:30:00", ID3: "2025-09-24 16:00:00"})
    links(c, ID2, ID1)
    rep = download.run(StepContext(settings, c, quiet=True))
    assert rep.done == ["2025-09-17_prova_lez01", "2025-09-18_prova_lez02"]
    lay = Layout.of(c)
    assert (lay.video / "2025-09-17_prova_lez01.mp4").exists()

    fake.info_calls.clear()
    rep2 = download.run(StepContext(settings, c, quiet=True))          # nulla di nuovo: nessuna chiamata a Webex
    assert rep2.done == [] and len(rep2.skipped) == 2 and fake.info_calls == []

    links(c, ID2, ID1, ID3)                                            # nuova: numerazione continua, scarica solo quella
    rep3 = download.run(StepContext(settings, c, quiet=True))
    assert rep3.done == ["2025-09-24_prova_lez03"] and fake.info_calls == [[ID3]] and fake.downloaded[-1] == ID3


def test_audio_format_goes_to_audio_and_numbering_spans_both(settings, course_with_links, monkeypatch):
    c = course_with_links
    FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00", ID2: "2025-09-18 10:00:00"})
    links(c, ID1)
    download.run(StepContext(settings, c, quiet=True))                                   # video
    links(c, ID1, ID2)
    rep = download.run(StepContext(settings, c, quiet=True, options={"formato": "audio"}))
    lay = Layout.of(c)
    assert rep.done == ["2025-09-18_prova_lez02"] and (lay.audio / "2025-09-18_prova_lez02.mp3").exists()
    assert not (lay.video / "2025-09-18_prova_lez02.mp4").exists()
    c.extra["formato"] = "boh"
    assert download.run(StepContext(settings, c, quiet=True)).error


def test_jobs_for_formats():
    r = webex.Recording(id=ID1, name="", created=datetime(2026, 1, 1), created_gmt=None, url="https://cdn/x.mp4",
                        audio_url="https://cdn/x.mp3")
    assert webex.jobs_for(r, "s").name == "s.mp4" and webex.jobs_for(r, "s", "audio").name == "s.mp3"
    r.hls, r.audio_url, r.url = True, None, "https://cdn/x.m3u8"
    j = webex.jobs_for(r, "s", "audio")
    assert j.stream and j.audio_only and j.name == "s.m4a"
    assert webex.jobs_for(r, "s").stream


def test_legacy_manifest_keys_are_recognised_and_relinked(settings, course_with_links, monkeypatch):
    import json
    c = course_with_links
    lay = Layout.of(c)
    lay.state.mkdir(parents=True)
    (lay.state / "manifest.json").write_text(json.dumps({"videos": {"2025-09-17 09-30": "2025-09-17_prova_lez01"}}))
    fake = FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00"})
    links(c, ID1)
    rep = download.run(StepContext(settings, c, quiet=True))
    assert rep.done == [] and rep.skipped == ["2025-09-17_prova_lez01"] and not fake.downloaded
    assert json.loads((lay.state / "manifest.json").read_text())["videos"][ID1] == "2025-09-17_prova_lez01"


def test_dry_run_does_not_download(settings, course_with_links, monkeypatch):
    fake = FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00"})
    links(course_with_links, ID1)
    rep = download.run(StepContext(settings, course_with_links, dry_run=True, quiet=True))
    assert rep.done == ["2025-09-17_prova_lez01"] and rep.dry_run
    assert not fake.downloaded and not Layout.of(course_with_links).video.exists()


def test_enriched_links_file_sets_type_topic_and_date(settings, course_with_links, monkeypatch):
    import json
    c = course_with_links
    links(c, extra="# commento\n"
          f"{URL.format(ID1)}\t08/05/2026 10:33\tLezione\tDF Trasporto 4\n"
          f"{URL.format(ID2)}\t07/05/2026 10:31\tLaboratorio\tLab 3\n")
    FakeWebex(monkeypatch, {ID1: "2026-05-10 01:45:00", ID2: "2026-05-16 17:20:00"})   # Webex: date sbagliate
    rep = download.run(StepContext(settings, c, quiet=True))
    assert sorted(rep.done) == ["2026-05-07_prova_lab01", "2026-05-08_prova_lez01"]
    meta = json.loads((c.cartella / ".sbob" / "manifest.json").read_text())["meta"]
    assert meta["2026-05-08_prova_lez01"]["argomento"] == "DF Trasporto 4"


def test_expired_ticket_renews_once_then_needs_human(settings, course_with_links, monkeypatch):
    c = course_with_links
    links(c, ID1)
    fake = FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00"}, expired_first=1)
    rep = download.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert rep.done == ["2025-09-17_prova_lez01"] and fake.renewed == 1
    FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00"}, expired_first=2)
    with pytest.raises(NeedsHuman) as e:
        download.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert e.value.action == "sbob login"


def test_missing_ticket_needs_login(settings, course_with_links, monkeypatch):
    from sbob.core import secrets
    links(course_with_links, ID1)
    monkeypatch.setattr(secrets, "load_cookie", lambda name: None)
    monkeypatch.setattr(download, "try_renew_login", lambda ctx: False)
    with pytest.raises(NeedsHuman) as e:
        download.run(StepContext(settings, course_with_links, dry_run=True, quiet=True))
    assert e.value.action == "sbob login"


def test_multiple_sources_merge_dedupe_and_survive_a_failing_one(settings, course_with_links, monkeypatch):
    c = course_with_links
    c.sorgenti = [{"tipo": "txt", "file": "link.txt"}, {"tipo": "webpage-url", "url": "https://prof.example/lezioni"},
                  {"tipo": "webeep", "url": "https://webeep.polimi.it/course/view.php?id=7"}]
    links(c, ID1)
    page = f'<a href="{URL.format(ID1)}">uno</a> <a href="https://www.google.com/url?q={URL.format(ID2)}&sa=D">due</a>' \
           '<a href="https://politecnicomilano.webex.com/wbxmjs/joinservice/sites/x/meeting/download/abc">aula</a>'

    class R:
        status_code, text = 200, page
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())

    def broken(course, src):
        raise NeedsHuman("Token WeBeep mancante", action="sbob login")
    monkeypatch.setattr(download, "webeep_links", broken)
    FakeWebex(monkeypatch, {ID1: "2025-09-17 09:30:00", ID2: "2025-09-24 10:00:00"})
    rep = download.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert rep.done == ["2025-09-17_prova_lez01", "2025-09-24_prova_lez02"]        # unione senza duplicati, aula esclusa
    assert len(rep.warnings) == 1 and "fonte 3 (webeep)" in rep.warnings[0] and rep.exit_code == 2


def test_all_sources_failing_is_an_error(settings, course_with_links, monkeypatch):
    c = course_with_links
    c.sorgenti = [{"tipo": "webeep"}]
    c.webeep_id = None
    with pytest.raises(NeedsHuman):
        download.run(StepContext(settings, c, dry_run=True, quiet=True))


def test_webeep_source_reads_url_modules_and_types(settings, course_with_links, monkeypatch):
    from sbob.webeep import client as wc
    c = course_with_links
    c.webeep_id = 7
    sections = [{"section": 1, "summary": "", "modules": [
        {"id": 10, "modname": "url", "name": "2025-09-19 Lez 01 - introduzione",
         "contents": [{"type": "url", "fileurl": URL.format(ID1)}]},
        {"id": 11, "modname": "url", "name": "Esercitazione 1",
         "contents": [{"type": "url", "fileurl": URL.format(ID2)}]},
        {"id": 12, "modname": "url", "name": "Aula virtuale",
         "contents": [{"type": "url", "fileurl": "https://politecnicomilano.webex.com/meet/x"}]}]},
        {"section": 2, "summary": f'<a href="{URL.format(ID3)}">x</a>', "modules": []}]

    class C:
        def __init__(self, token): pass
        def call(self, fn, **kw):
            assert kw["courseid"] == 7
            return sections
    monkeypatch.setattr(wc, "WebeepClient", C)
    got = download.webeep_links(c, {"tipo": "webeep"})
    assert [(u[-41:-9], m.get("tipo")) for u, m in got] == [(ID1, "lez"), (ID2, "ese"), (ID3, None)]
    only_sec = download.webeep_links(c, {"tipo": "webeep", "url": "https://webeep.polimi.it/course/view.php?id=7&section=2"})
    assert len(only_sec) == 1


def test_video_id_and_links_in_html():
    http = None
    assert webex.video_id(URL.format(ID1), http) == ID1
    assert webex.video_id(f"https://politecnicomilano.webex.com/webappng/sites/politecnicomilano/recording/{ID2}", http) == ID2
    assert webex.video_id(ID3, http) == ID3
    assert webex.video_id("https://politecnicomilano.webex.com/meet/prof", http) is None
    html = f'<a href="https://www.google.com/url?q={URL.format(ID1)}&amp;sa=D">a</a> <a href=\'{URL.format(ID1)}\'>b</a>'
    assert webex.links_in_html(html) == [URL.format(ID1)]


def test_tipo_from_title():
    assert download.tipo_from_title("2025-09-19 Lez 01 - introduzione") == "lez"
    assert download.tipo_from_title("Es. 3 - integrali") == "ese" and download.tipo_from_title("Laboratorio 2") == "lab"
    assert download.tipo_from_title("Registrazione del 3 marzo") is None


def test_recording_api_errors(monkeypatch):
    class Resp:
        def __init__(self, status, data, ctype="application/json"):
            self.status_code, self._d, self.headers = status, data, {"content-type": ctype}
        def json(self): return self._d

    class H:
        def __init__(self, r): self.r = r
        def get(self, *a, **k): return self.r
    with pytest.raises(webex.TicketError):
        webex.recording(ID1, H(Resp(403, {"code": 53004, "message": "Recording required logged before access"})))
    with pytest.raises(webex.TicketError):
        webex.recording(ID1, H(Resp(200, {}, "text/html")))
    ok = {"recordName": "x", "createTime": "2026-06-04 12:32:30", "gmtCreateTime": "2026-06-04 10:32:30",
          "preventDownload": False, "duration": 2525000, "fileSize": 9,
          "downloadRecordingInfo": {"downloadInfo": {"mp4URL": "https://cdn/x.mp4", "hlsURL": "https://cdn/x.m3u8"}}}
    r = webex.recording(ID1, H(Resp(200, ok)))
    assert r.url.endswith(".mp4") and not r.hls and r.legacy_key == "2026-06-04 12-32" and r.duration_s == 2525
    r = webex.recording(ID1, H(Resp(200, {**ok, "preventDownload": True})))
    assert r.hls and r.url.endswith(".m3u8")


def test_config_accepts_list_or_single_source(tmp_path, monkeypatch):
    from sbob.config import load_settings
    cfg = tmp_path / "sbob.toml"
    cfg.write_text('''root = "%s"
[corsi.a]
nome = "A"
anno_accademico = "2025-26"
sorgente = { tipo = "txt", file = "x.txt" }
[corsi.b]
nome = "B"
anno_accademico = "2025-26"
sorgenti = [ { tipo = "webeep", url = "u" }, { tipo = "webpage-url", url = "https://prof" } ]
''' % tmp_path)
    s = load_settings(cfg)
    assert [x["tipo"] for x in s.corso("a").sorgenti] == ["txt"] and s.corso("a").sorgente["tipo"] == "txt"
    assert [x["tipo"] for x in s.corso("b").sorgenti] == ["webeep", "webpage-url"]


def test_archivio_source_writes_link_file_and_reuses_txt_path(settings, course_with_links, monkeypatch):
    from sbob.auth import recman
    c = course_with_links
    c.sorgenti = [{"tipo": "archivio", "url": "https://aunicalogin.polimi.it/aunicalogin/getservizio.xml?id_servizio=2294&c_classe_webeep=1-STD"}]
    rows = [{"webex": f"https://politecnicomilano.webex.com/recordingservice/sites/x/recording/{ID1}/playback",
             "data": "08/05/2026 10:33", "forma": "Laboratorio", "argomento": "Lab 3"}]
    monkeypatch.setattr(recman, "collect", lambda entry, headless=True, log=None: rows)
    FakeWebex(monkeypatch, {ID1: "2026-05-10 01:45:00"})
    rep = download.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert rep.done == ["2026-05-08_prova_lab01"]                       # data e tipo dall'archivio
    text = (c.cartella / "link_archivio.txt").read_text()
    assert ID1 in text and "Laboratorio\tLab 3" in text                  # file riusabile a mano (fallback)


def test_archivio_without_course_link_needs_human(settings, course_with_links):
    c = course_with_links
    c.sorgenti, c.webeep_id = [{"tipo": "archivio"}], None
    with pytest.raises(NeedsHuman) as e:
        download.run(StepContext(settings, c, dry_run=True, quiet=True))
    assert "webeep collega" in e.value.action


def test_recman_helpers():
    from sbob.auth.recman import archive_entries, is_archive_url
    good = "https://aunicalogin.polimi.it/aunicalogin/getservizio.xml?id_servizio=2294&c_classe_webeep=890169-STD"
    assert is_archive_url(good) and not is_archive_url("https://aunicalogin.polimi.it/aunicalogin/getservizio.xml?id_servizio=2292")

    class C:
        def call(self, fn, **kw):
            return [{"modules": [{"modname": "url", "contents": [{"fileurl": good}]},
                                 {"modname": "url", "contents": [{"fileurl": "https://altro.it"}]},
                                 {"modname": "resource", "contents": [{"fileurl": good}]}]}]
    assert archive_entries(C(), 1) == [good]
