import pytest

fitz = pytest.importorskip("pymupdf")

from sbob.core import frontmatter  # noqa: E402
from sbob.llm.base import ErrorKind, FilePart, ImagePart, LLMResult  # noqa: E402
from sbob.tools import pdf2md  # noqa: E402
from sbob.tools.pdf2md import _blocks, convert_pdf  # noqa: E402


class FakeRole:
    """Risponde con una riga per pagina; può far fallire blocchi multipagina o singole pagine."""
    label, model, workers = "fake/m", "m", 2

    def __init__(self, tipo="gemini", fail_blocks=False, fail_pages=(), conf=None):
        self.provider_conf, self.conf = {"tipo": tipo}, conf or {}
        self.fail_blocks, self.fail_pages, self.seen = fail_blocks, set(fail_pages), []

    def complete(self, messages, item=None, validate=None, **kw):
        assert kw["system"]
        first, last = (int(x) for x in item.rsplit("#p", 1)[1].split("-"))
        parts = messages[0].parts
        self.seen.append((first, last, tuple(type(p).__name__ for p in parts)))
        if self.fail_blocks and last > first:
            return LLMResult(error_kind=ErrorKind.OTHER, error="risposta troncata")
        if any(p in self.fail_pages for p in range(first, last + 1)):
            return LLMResult(error_kind=ErrorKind.OTHER, error="pagina illeggibile")   # problema del blocco → split
        return LLMResult(text="\n".join(f"## Pagina {p}" for p in range(first, last + 1)))


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "dispensa.pdf"
    doc = fitz.open()
    for i in range(1, 6):
        doc.new_page().insert_text((72, 72), f"Contenuto pagina {i}")
    doc.save(p)
    return p


def test_blocks_group_consecutive_pages():
    assert _blocks([0, 1, 2, 3, 4], 2) == [(0, 1), (2, 3), (4, 4)]
    assert _blocks([0, 2, 3, 7], 8) == [(0, 0), (2, 3), (7, 7)]


def test_gemini_gets_native_subpdfs(pdf, tmp_path):
    out = tmp_path / "md" / "dispensa.md"
    role = FakeRole("gemini")
    r = convert_pdf(pdf, out, role, meta={"corso": "X"}, pages_per_block=2)
    assert r.complete and r.converted == 5
    assert sorted((a, b) for a, b, _ in role.seen) == [(1, 2), (3, 4), (5, 5)]
    assert all(kinds == ("FilePart", "TextPart") for *_, kinds in role.seen)
    meta, body = frontmatter.read(out)
    assert meta == {"corso": "X", "conversione": "visione"} and [l for l in body.splitlines() if l] == [f"## Pagina {i}" for i in range(1, 6)]
    assert not list((out.parent / ".checkpoints").rglob("*.md"))          # checkpoint puliti a fine lavoro


def test_openai_compatible_gets_page_images(pdf, tmp_path):
    role = FakeRole("openai")
    convert_pdf(pdf, tmp_path / "o.md", role, pages_per_block=3)
    kinds = {(a, b): k for a, b, k in role.seen}
    assert kinds[(1, 3)] == ("ImagePart", "ImagePart", "ImagePart", "TextPart")


def test_subpdf_has_right_pages(pdf, tmp_path):
    msg = pdf2md._message(pdf, 1, 2, 5, "pdf", "it", tmp_path)
    fp = next(p for p in msg.parts if isinstance(p, FilePart))
    with fitz.open(fp.path) as sub:
        assert len(sub) == 2 and "Contenuto pagina 2" in sub.load_page(0).get_text()
    img = pdf2md._message(pdf, 0, 0, 5, "immagini", "it", tmp_path).parts[0]
    assert isinstance(img, ImagePart) and img.data[:4] == b"\x89PNG"


def test_failed_block_falls_back_to_single_pages(pdf, tmp_path):
    role = FakeRole(fail_blocks=True)
    r = convert_pdf(pdf, tmp_path / "o.md", role, pages_per_block=5)
    assert r.complete and [(a, b) for a, b, _ in role.seen] == [(1, 5), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]


def test_failed_page_is_retried_next_run_and_checkpoints_survive(pdf, tmp_path):
    out = tmp_path / "md" / "dispensa.md"
    r = convert_pdf(pdf, out, FakeRole(fail_pages={3}), pages_per_block=2)
    assert not r.complete and list(r.failed) == [3] and not out.exists()
    role = FakeRole()
    r = convert_pdf(pdf, out, role, pages_per_block=2)
    assert r.complete and r.resumed == 4 and [(a, b) for a, b, _ in role.seen] == [(3, 3)]
    assert "## Pagina 3" in out.read_text()


def test_force_and_skip_existing(pdf, tmp_path):
    out = tmp_path / "o.md"
    out.write_text("vecchio")
    role = FakeRole()
    assert convert_pdf(pdf, out, role).complete and role.seen == []
    assert convert_pdf(pdf, out, role, force=True).converted == 5 and "vecchio" not in out.read_text()


def test_mode_override(pdf):
    assert pdf2md.pdf_mode(FakeRole("openai", conf={"pdf_modo": "pdf"})) == "pdf"
    assert pdf2md.pdf_mode(FakeRole("anthropic")) == "pdf"


def test_server_overload_does_not_split_into_pages(pdf, tmp_path):
    class Overloaded(FakeRole):
        def complete(self, messages, item=None, validate=None, **kw):
            first, last = (int(x) for x in item.rsplit("#p", 1)[1].split("-"))
            self.seen.append((first, last, ()))
            return LLMResult(error_kind=ErrorKind.SERVER, error="503 high demand")
    role = Overloaded()
    r = convert_pdf(pdf, tmp_path / "o.md", role, pages_per_block=5)
    assert role.seen == [(1, 5, ())] and sorted(r.failed) == [1, 2, 3, 4, 5]     # una sola chiamata, niente split


class QuotaRole(FakeRole):
    """Visione: finisce la quota dopo `ok_calls` chiamate; poi NeedsHuman."""
    def __init__(self, ok_calls):
        super().__init__()
        self.ok_calls = ok_calls

    def complete(self, messages, item=None, validate=None, **kw):
        from sbob.core.report import NeedsHuman
        if len(self.seen) >= self.ok_calls:
            raise NeedsHuman("Quota esaurita")
        return super().complete(messages, item, validate, **kw)


class TextRole:
    label, model, workers = "deepseek/flash", "flash", 1

    def __init__(self):
        self.seen = []

    def complete(self, messages, item=None, validate=None, **kw):
        assert "PAGINA" in messages[0].parts[0].text and kw["system"]
        first, last = (int(x) for x in item.rsplit("#t", 1)[1].split("-"))
        self.seen.append((first, last))
        return LLMResult(text="\n".join(f"testo {p}" for p in range(first, last + 1)))


def test_quota_exhausted_falls_back_to_text_and_upgrades_later(pdf, tmp_path):
    out = tmp_path / "md" / "dispensa.md"
    ckpt = tmp_path / "ck"
    text = TextRole()
    r = convert_pdf(pdf, out, QuotaRole(ok_calls=1), pages_per_block=2, text_role=text, checkpoint_dir=ckpt, workers=1)
    assert r.complete and r.degraded == [3, 4, 5]                    # blocco 1-2 con la visione, il resto solo testo
    meta, body = frontmatter.read(out)
    assert meta["conversione"] == "misto" and "## Pagina 1" in body and "testo 3" in body
    assert ckpt.exists()                                              # i checkpoint restano per l'upgrade

    role = FakeRole()                                                 # quota tornata: upgrade delle sole pagine solo-testo
    r2 = convert_pdf(pdf, out, role, pages_per_block=2, text_role=text, checkpoint_dir=ckpt, upgrade=True, workers=1)
    assert r2.complete and r2.degraded == [] and sorted((a, b) for a, b, _ in role.seen) == [(3, 4), (5, 5)]
    meta, body = frontmatter.read(out)
    assert meta["conversione"] == "visione" and "testo 3" not in body and not ckpt.exists()


def test_quota_without_text_role_still_raises(pdf, tmp_path):
    import pytest
    from sbob.core.report import NeedsHuman
    with pytest.raises(NeedsHuman):
        convert_pdf(pdf, tmp_path / "o.md", QuotaRole(0), pages_per_block=2, workers=1)
