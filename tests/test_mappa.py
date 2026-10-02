import json

from sbob.core import frontmatter, index
from sbob.core.layout import Layout
from sbob.core.search import search
from sbob.core.verify import verify_course
from sbob.llm.base import LLMResult
from sbob.steps import mappa as mappa_mod
from sbob.steps.base import StepContext

L1, L2 = "2025-09-17_prova_lez01", "2025-09-24_prova_lez03"


def card(riassunto, concetti, prereq=()):
    return json.dumps({"riassunto": riassunto, "concetti": concetti, "prerequisiti": list(prereq)})


class FakeRole:
    label, model, n_keys, workers = "fake/m", "m", 1, 1

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, messages, item=None, validate=None, **kw):
        """Come il registry: ritenta finché il validatore accetta (o finiscono le risposte)."""
        self.prompts.append(messages[0].parts[0].text)
        while self.replies:
            text = self.replies.pop(0)
            if not (validate and validate(text)):
                return LLMResult(text=text, model=self.model)
        return LLMResult(error_kind="other", error="JSON non valido")


def _course(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("appunti", "trascrizioni")
    for stem, body in ((L1, "## Diodi\n\nLa giunzione pn e il diodo ideale."),
                       (L2, "## Transistor\n\nIl BJT usa due giunzioni pn. [12:30] Polarizzazione.")):
        (lay.trascrizioni / f"{stem}.md").write_text(frontmatter.join({"lezione": stem}, body))
        (lay.appunti / f"{stem}_appunti.md").write_text(frontmatter.join({"lezione": stem}, body))
    return c, lay


def test_parse_card_tolerant():
    assert mappa_mod.parse_card('```json\n{"riassunto": "x", "concetti": ["A", {"nome": "B", "ruolo": "introdotto"}]}\n```') == \
        {"riassunto": "x", "concetti": [{"nome": "A", "ruolo": "ripreso"}, {"nome": "B", "ruolo": "introdotto"}],
         "prerequisiti": []}
    assert mappa_mod.parse_card("non json") is None
    assert mappa_mod.parse_card('{"concetti": []}') is None


def test_mappa_builds_cards_index_and_concepts(settings, monkeypatch):
    c, lay = _course(settings)
    role = FakeRole([card("Diodi.", [{"nome": "Giunzione pn", "ruolo": "introdotto"}], ["Semiconduttori"]),
                     "boh, niente json",
                     card("BJT.", [{"nome": "Giunzione pn", "ruolo": "ripreso"}, {"nome": "BJT", "ruolo": "introdotto"}],
                          ["Giunzione pn"])])
    monkeypatch.setattr(mappa_mod.Registry, "role", lambda self, n, o=None: role)
    rep = mappa_mod.run(StepContext(settings, c, quiet=True))
    assert rep.done == [L1, L2] and not rep.failed
    assert "Giunzione pn" in role.prompts[-1]                       # i concetti noti vengono passati al modello
    appunti_before = (lay.appunti / f"{L1}_appunti.md").read_text()

    idx = (lay.mappa / "INDICE.md").read_text()
    assert "Lezione 01 · 2025-09-17" in idx and "Diodi." in idx and "**Prerequisiti:** Semiconduttori" in idx
    assert "(<../appunti/2025-09-17_prova_lez01_appunti.md>)" in idx
    gpn = (lay.mappa / "concetti" / "Giunzione pn.md").read_text()
    assert "## Introdotto in" in gpn and "## Ripreso in" in gpn and "## Prerequisito per" in gpn
    assert (settings.root / "mappa" / "INDICE.md").exists()
    cat = json.loads((settings.root / "mappa" / "catalog.json").read_text())
    assert cat["corsi"][0]["lezioni"][1]["concetti"][1]["nome"] == "BJT"

    # appunti intatti; secondo giro: nulla da rifare
    assert (lay.appunti / f"{L1}_appunti.md").read_text() == appunti_before
    rep2 = mappa_mod.run(StepContext(settings, c, quiet=True))
    assert rep2.skipped == [L1, L2] and rep2.done == []


def test_search_finds_paragraph_with_section_and_minute(settings):
    c, _ = _course(settings)
    index.save_schede(c, {L2: {"riassunto": "Il transistor bipolare.", "concetti": [{"nome": "BJT"}], "hash": "x"}})
    res = search(settings, "giunzioni polarizzazione")
    assert res["totale"] == 2                                       # stesso paragrafo in appunti e trascrizioni
    h = res["risultati"][0]
    assert h["lezione"] == L2 and h["sezione"] == "Transistor" and h["minuto"] == "12:30" and h["data"] == "2025-09-24"
    assert search(settings, "GIUNZION")["totale"] == 4              # maiuscole non contano
    assert search(settings, "polarizzazióne")["totale"] == 2       # accenti non contano
    assert search(settings, '"diodo ideale"', dove=("appunti",))["totale"] == 1
    assert search(settings, "transistor bipolare", dove=("mappa",))["risultati"][0]["fonte"] == "mappa"


def test_verify_flags_lost_content_and_gaps(settings):
    c, lay = _course(settings)
    (lay.trascrizioni / f"{L2}.md").write_text("parola " * 1000)   # appunti di L2 ora cortissimi
    (lay.appunti / "2025-09-30_prova_lez04.parziale.md").write_text("x")
    d = verify_course(c, check_audio=False)
    problems = {(i["livello"], i["lezione"]): i["problema"] for i in d["problemi"]}
    assert "più corti" in problems[("errore", L2)]
    assert ("errore", "2025-09-30_prova_lez04") in problems
    assert any("mancano lez02" in i["problema"] for i in d["problemi"])
    assert ("info", L1) in problems                                 # manca la scheda nella mappa
