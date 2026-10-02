"""Backend notebooklm (extra): carica gli audio in un taccuino NotebookLM e ne estrae il testo (`source fulltext`).

Guida il CLI `notebooklm` già installato (notebooklm-py), con argomenti in lista (niente shell).
Vincoli rispettati: max 10 sorgenti in volo, timeout 12 min per sorgente, max 3 tentativi per file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sbob import notebooklm_cli
from sbob.core.batch import Job
from sbob.core.report import StepReport
from sbob.steps.base import StepContext
from sbob.steps.transcribe.base import write_transcript

MAX_IN_FLIGHT = 10
SOURCE_TIMEOUT = 12 * 60
MAX_ATTEMPTS = 3
POLL_SECONDS = 30


def _nb(*args: str) -> tuple[str, str, int]:
    return notebooklm_cli.nb(*args)         # indirezione: i test sostituiscono questa funzione


_json = notebooklm_cli.parse_json
_check_auth = notebooklm_cli.check_auth


def find_or_create_notebook(title: str) -> str:
    out, err, rc = _nb("list", "--json")
    _check_auth(err, rc)
    if rc != 0:
        raise RuntimeError(f"notebooklm list fallito: {err}")
    for nb in _json(out).get("notebooks", []):
        if nb.get("title") == title:
            return nb["id"]
    out, err, rc = _nb("create", title, "--json")
    _check_auth(err, rc)
    nb_id = _json(out).get("notebook", {}).get("id")
    if rc != 0 or not nb_id:
        raise RuntimeError(f"creazione taccuino fallita: {err or out}")
    return nb_id


@dataclass
class _State:
    job: Job
    attempts: int = 0
    added_at: float | None = None     # None = non ancora caricato


def transcribe_many(ctx: StepContext, jobs: list[Job], rep: StepReport, *, poll: float = POLL_SECONDS) -> None:
    clock, sleep = time.monotonic, time.sleep   # letti a runtime: i test possono sostituirli
    title = ctx.course.notebook or f"Sbobine {ctx.course.nome}"
    nb = find_or_create_notebook(title)
    ctx.log(f"notebooklm: taccuino '{title}' ({nb}), {len(jobs)} file")
    pending = {j.src.name: _State(j) for j in jobs}

    def drop(name: str, sid: str | None, st: _State, reason: str) -> None:
        if sid:
            _nb("source", "delete", sid, "-n", nb, "--yes")
        st.attempts += 1
        st.added_at = None
        if st.attempts >= MAX_ATTEMPTS:
            rep.fail(st.job.dst.stem, f"{reason} ({MAX_ATTEMPTS} tentativi)")
            del pending[name]
        else:
            ctx.log(f"notebooklm: {name}: {reason}, ritento")

    while pending:
        out, err, rc = _nb("source", "list", "-n", nb, "--json")
        _check_auth(err, rc)
        sources = {s.get("title"): s for s in _json(out).get("sources", [])} if rc == 0 else {}
        in_flight = len(sources)

        for name, st in list(pending.items()):
            src = sources.get(name)
            if src is None:
                if st.added_at is not None:      # caricato ma sparito: riparte
                    st.added_at = None
                if in_flight < MAX_IN_FLIGHT:
                    _, e, rc = _nb("source", "add", str(st.job.src), "-n", nb, "--json")
                    _check_auth(e, rc)
                    if rc == 0:
                        st.added_at = clock()
                        in_flight += 1
                    else:
                        drop(name, None, st, f"upload fallito: {e}")
                continue
            sid, status = src.get("id"), src.get("status")
            if status == "ready":
                o, e, rc = _nb("source", "fulltext", sid, "-n", nb, "--json")
                content = _json(o).get("content") if rc == 0 else None
                if content:
                    write_transcript(ctx, st.job, content, rep, backend="notebooklm")
                    _nb("source", "delete", sid, "-n", nb, "--yes")
                    del pending[name]
                else:
                    drop(name, sid, st, f"fulltext fallito: {e or 'vuoto'}")
            elif status == "error":
                drop(name, sid, st, "errore di elaborazione")
            elif st.added_at is not None and clock() - st.added_at > SOURCE_TIMEOUT:
                drop(name, sid, st, "timeout")
            elif st.added_at is None:           # già presente da un run precedente: parte il timeout da ora
                st.added_at = clock()
        if pending:
            sleep(poll)
