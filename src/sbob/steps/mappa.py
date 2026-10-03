"""Passo mappa: per ogni lezione con appunti, una scheda per l'agente (riassunto, concetti, prerequisiti)
in <corso>/mappa/schede.json; poi rigenera gli indici (core.index). Gli appunti non vengono toccati.

Le lezioni sono elaborate in ordine di data, passando al modello i concetti già noti del corso: così i nomi restano
uniformi ("Giunzione pn" e non anche "giunzione p-n") e i link tra lezioni funzionano.
Una scheda si rifà solo se gli appunti sono cambiati (hash) o con --force.
"""

from __future__ import annotations

import hashlib
import json
import re

from sbob.core import frontmatter, index, naming, prompts
from sbob.core.batch import list_inputs
from sbob.core.report import StepReport
from sbob.llm.base import Message
from sbob.llm.cost import CostTracker
from sbob.llm.registry import Registry, parse_model_override
from sbob.steps.base import StepContext

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_card(text: str) -> dict | None:
    """JSON della scheda, tollerando ```json ... ``` e testo attorno. None se non valido."""
    raw = _FENCE.sub("", text.strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("riassunto"), str):
        return None
    concetti = []
    for c in data.get("concetti", []):
        if isinstance(c, str):
            c = {"nome": c, "ruolo": "ripreso"}
        if isinstance(c, dict) and str(c.get("nome", "")).strip() and not index.is_generic_concept(str(c["nome"])):
            concetti.append({"nome": str(c["nome"]).strip(),
                             "ruolo": "introdotto" if c.get("ruolo") == "introdotto" else "ripreso"})
    prereq = [str(p).strip() for p in data.get("prerequisiti", [])
              if str(p).strip() and not index.is_generic_concept(str(p))][:5]
    return {"riassunto": data["riassunto"].strip(), "concetti": concetti[:10], "prerequisiti": prereq}


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("mappa")
    lay, course = ctx.layout, ctx.course
    files = [p for p in list_inputs(lay.appunti, [".md"]) if p.stem.endswith(naming.NOTES_SUFFIX)]
    schede = index.load_schede(course)

    todo = []
    for p in files:
        stem = p.stem.removesuffix(naming.NOTES_SUFFIX)
        if ctx.only and stem not in ctx.only:
            continue
        _, body = frontmatter.read(p)
        h = hashlib.sha256(body.encode()).hexdigest()[:16]
        if not ctx.force and schede.get(stem, {}).get("hash") == h:
            rep.skipped.append(stem)
        else:
            todo.append((stem, body, h))
    if ctx.dry_run:
        rep.done = [s for s, _, _ in todo]
        return rep

    if todo:
        tracker = CostTracker(log_path=lay.costs)
        role = Registry(ctx.settings, tracker).role("mappa", parse_model_override(ctx.options.get("modello")))
        try:
            for stem, body, h in todo:          # in ordine di data: il vocabolario dei concetti cresce lezione per lezione
                known = sorted({c["nome"] for s, sc in schede.items() if s != stem for c in sc.get("concetti", [])},
                               key=str.casefold)
                prompt = prompts.render(course.lingua, "mappa", corso=course.nome, lezione=stem, testo=body,
                                        concetti_esistenti="\n".join(f"- {k}" for k in known) or "(nessuno)")
                res = role.complete([Message.user(prompt)], item=stem,
                                    validate=lambda t: None if parse_card(t) else "JSON non valido")
                card = parse_card(res.text or "") if res.ok else None
                if not card:
                    rep.fail(stem, res.error or "scheda non valida")
                    continue
                schede[stem] = {**card, "hash": h, "modello": res.model}
                index.save_schede(course, schede)
                rep.done.append(stem)
                ctx.log(f"mappa: {stem} → {len(card['concetti'])} concetti")
        finally:
            rep.cost = tracker.summary()

    rendered = index.render_all(ctx.settings)
    rep.outputs = [str(lay.mappa / "INDICE.md")] + ([rendered["indice_globale"]] if rendered["indice_globale"] else [])
    return rep
