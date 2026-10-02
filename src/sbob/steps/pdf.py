"""Passo pdf: PDF → Markdown (tools/pdf2md). Opzioni: path (file o cartella), out (cartella di destinazione).

Con un corso la destinazione di default è <corso>/materiale_md (da lì lo leggono ricerca e `sbob merge --da materiale`);
senza corso, ./markdown_output.
"""

from __future__ import annotations

from pathlib import Path

from sbob.core.batch import list_inputs
from sbob.core.report import NeedsHuman
from sbob.core.report import StepReport
from sbob.llm.cost import CostTracker
from sbob.llm.registry import Registry, parse_model_override
from sbob.steps.base import StepContext
from sbob.tools.pdf2md import DEFAULT_PAGES_PER_BLOCK, convert_pdf


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("pdf")
    target = Path(ctx.options["path"]).expanduser()
    out_dir = Path(ctx.options["out"]).expanduser() if ctx.options.get("out") else ctx.layout.materiale_md
    pdfs = list_inputs(target, [".pdf"]) if target.is_dir() else [target]
    pdfs = [p for p in pdfs if p.is_file() and (not ctx.only or p.stem in ctx.only)]
    if not pdfs:
        rep.error = f"nessun PDF in {target}"
        return rep
    if ctx.dry_run:
        rep.done = [p.stem for p in pdfs if ctx.force or not (out_dir / f"{p.stem}.md").exists()]
        rep.skipped = [p.stem for p in pdfs if p.stem not in rep.done]
        return rep

    tracker = CostTracker(log_path=ctx.layout.costs if ctx.course.slug != "pdf" else None)
    role = Registry(ctx.settings, tracker).role("pdf", parse_model_override(ctx.options.get("modello")))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for pdf in pdfs:
            out = out_dir / f"{pdf.stem}.md"
            existed = out.exists() and not ctx.force
            meta = {"corso": ctx.course.nome, "slug": ctx.course.slug, "anno": ctx.course.anno_accademico, "edizione": ctx.course.anno_accademico,
                    "fonte": pdf.name, "tipo": "materiale", "modello": role.model} if ctx.course.slug != "pdf" else {}
            try:
                r = convert_pdf(pdf, out, role, ctx.course.lingua, force=ctx.force, meta=meta, log=ctx.log,
                                pages_per_block=int(role.conf.get("pagine_per_blocco", DEFAULT_PAGES_PER_BLOCK)))
            except NeedsHuman:
                raise
            except Exception as e:  # noqa: BLE001
                rep.fail(pdf.stem, e)
                continue
            if existed:
                rep.skipped.append(pdf.stem)
            elif r.complete:
                rep.done.append(pdf.stem)
                rep.outputs.append(str(out))
            else:
                pages = ", ".join(map(str, sorted(r.failed)))
                rep.fail(pdf.stem, f"{len(r.failed)}/{r.pages} pagine fallite ({pages}); rilancia per riprovare")
    finally:
        rep.cost = tracker.summary()
    return rep
