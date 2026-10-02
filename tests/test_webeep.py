import pytest

from sbob.core.report import NeedsHuman
from sbob.webeep.client import RemoteFile, WebeepClient, safe_name, safe_path


class FakeResp:
    def __init__(self, data=None, chunks=()):
        self._data, self._chunks = data, chunks
    def json(self): return self._data
    def raise_for_status(self): pass
    def iter_content(self, n): return iter(self._chunks)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = responses, []
    def post(self, url, data=None, timeout=None):
        self.calls.append(data)
        return FakeResp(self.responses[data["wsfunction"]])
    def get(self, url, params=None, stream=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(chunks=[b"ab", b"cd"])


CONTENTS = [
    {"name": "Lezioni/Slide", "modules": [
        {"modname": "resource", "name": "Slide 01", "contents": [
            {"type": "file", "filename": "s1.pdf", "filepath": "/", "fileurl": "https://x/s1", "filesize": 4,
             "timemodified": 1700000000}]},
        {"modname": "folder", "name": "Esercizi", "contents": [
            {"type": "file", "filename": "e1.pdf", "filepath": "/sol/", "fileurl": "https://x/e1", "filesize": 9,
             "timemodified": 1700000001},
            {"type": "file", "filename": "../../etc/passwd", "filepath": "/", "fileurl": "https://x/p", "filesize": 1,
             "timemodified": 0}]},
        {"modname": "forum", "name": "Avvisi", "contents": [{"type": "file", "filename": "x", "fileurl": "u"}]},
        {"modname": "url", "name": "Link", "contents": None},
    ]},
]


def test_files_rules_and_sanitizing():
    c = WebeepClient("TOK", FakeHttp({"core_course_get_contents": CONTENTS}))
    files = {f.relpath: f for f in c.files(5)}
    assert "Lezioni_Slide/Slide 01.pdf" in files                      # resource singola: nome del modulo + estensione
    assert "Lezioni_Slide/Esercizi/sol/e1.pdf" in files               # altrimenti sottocartella = nome del modulo
    assert not any("Avvisi" in k or "Link" in k for k in files)       # moduli esclusi
    assert all(".." not in k.split("/") and not k.startswith("/") for k in files)   # niente path traversal dal server
    assert "Lezioni_Slide/Esercizi/_.._etc_passwd" in files                          # neutralizzato in un nome innocuo
    assert files["Lezioni_Slide/Slide 01.pdf"].modified == 1700000000


def test_safe_helpers():
    assert safe_name("a/b\\c") == "a_b_c" and safe_name("..") == "senza_nome" and safe_name("") == "senza_nome"
    assert safe_path("../../x/./y//z") == "x/y/z"


def test_courses_parse_year_and_sort():
    http = FakeHttp({"core_webservice_get_site_info": {"userid": 7},
                     "core_enrol_get_users_courses": [
                         {"id": 1, "fullname": "Analisi [2024-25]"}, {"id": 2, "fullname": "EDP [2025-26]"},
                         {"id": 3, "fullname": "Senza anno"}]})
    out = WebeepClient("T", http).courses()
    assert [c["id"] for c in out] == [2, 1, 3] and out[0]["anno"] == "2025-26" and out[2]["anno"] is None


def test_invalid_token_is_human_and_never_leaks():
    http = FakeHttp({"core_webservice_get_site_info": {"exception": "x", "errorcode": "invalidtoken", "message": "m"}})
    with pytest.raises(NeedsHuman) as e:
        WebeepClient("SEGRETO123", http).site_info()
    assert e.value.action == "sbob login" and "SEGRETO123" not in str(e.value)
    with pytest.raises(NeedsHuman):
        WebeepClient("", http)


def test_download_atomic_and_mtime(tmp_path):
    http = FakeHttp({})
    f = RemoteFile("S", "", "a.pdf", "https://x/a", 4, 1700000000)
    dst = tmp_path / "S" / "a.pdf"
    WebeepClient("T", http).download(f, dst)
    assert dst.read_bytes() == b"abcd" and int(dst.stat().st_mtime) == 1700000000
    assert http.calls[0][1] == {"token": "T"} and not list(dst.parent.glob(".dl-*"))   # nessun temporaneo residuo
