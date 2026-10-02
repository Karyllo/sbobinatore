"""Archivio registrazioni del Poli (recman) letto dal browser di `sbob login`, senza codici da copiare.

recman usa codici di sessione monouso (`__pj1`) e accetta solo una sessione di browser vera: fuori dal browser
risponde POLIJ_049001. Il punto d'ingresso stabile è il modulo "Archivio registrazioni" di ogni corso WeBeep:
  https://aunicalogin.polimi.it/aunicalogin/getservizio.xml?id_servizio=2294&c_classe_webeep=<classe>
che passa dall'accesso di Ateneo e apre l'archivio di quel corso con una sessione nuova.

Il risultato è un file di link arricchito (`link<TAB>data<TAB>forma<TAB>argomento`, lo stesso formato di link.txt):
se l'automazione un giorno non funziona più, quel file resta e si può sempre scrivere a mano.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from sbob.core.batch import atomic_write_text
from sbob.core.report import NeedsHuman

RECMAN_SERVICE = "2294"                                     # id_servizio dell'archivio registrazioni
_WEBEX_RX = re.compile(r"https://[a-z0-9.-]*webex\.com/\S*?/recording/(?:playback/)?([0-9a-f]{32})", re.I)


def is_archive_url(url: str) -> bool:
    q = parse_qs(urlparse(url).query)
    return "getservizio" in url and q.get("id_servizio") == [RECMAN_SERVICE] and "c_classe_webeep" in q


def archive_entries(client, course_id: int) -> list[str]:
    """Link d'ingresso all'archivio presenti nel corso WeBeep (moduli URL verso il servizio recman)."""
    out = []
    for section in client.call("core_course_get_contents", courseid=course_id):
        for module in section.get("modules", []):
            if module.get("modname") != "url":
                continue
            for c in module.get("contents") or []:
                if is_archive_url(c.get("fileurl", "")):
                    out.append(c["fileurl"])
    return list(dict.fromkeys(out))


def _rows_js() -> str:
    return """() => [...document.querySelectorAll('tbody.TableDati-tbody tr')].map(tr => {
        const td = tr.querySelectorAll('td');
        return {data: td[1]?.innerText.trim(), forma: td[3]?.innerText.trim(),
                argomento: (td[4]?.innerText || '').trim().replace(/\\s+/g, ' '),
                preview: td[0]?.querySelector('a.Link')?.href};
    })"""


def collect(entry: str, headless: bool = True, log: Callable[[str], None] = lambda m: None) -> list[dict]:
    """Apre l'archivio e restituisce [{webex, data, forma, argomento}] per tutte le registrazioni."""
    from sbob.auth.browser import browser

    with browser(headless) as ctx:
        page = ctx.new_page()
        if "webeep.polimi.it/mod/url" in entry:                  # link al modulo WeBeep: si prende il link dentro
            page.goto(entry, timeout=60_000)
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            entry = next((h for h in hrefs if is_archive_url(h)), "")
            if not entry:
                raise RuntimeError("nel modulo WeBeep non c'è il link all'archivio registrazioni")
        page.goto(entry, timeout=60_000)
        try:
            page.wait_for_selector("tbody.TableDati-tbody", timeout=45_000)
        except Exception:  # noqa: BLE001
            if "aunicalogin" in page.url or "shibboleth" in page.url or "login" in page.url.lower():
                raise NeedsHuman("Sessione di Ateneo scaduta", action="sbob login") from None
            if re.search(r"POLIJ_\d+", page.content()):
                raise RuntimeError("l'archivio ha risposto con un errore di sessione (POLIJ)") from None
            # archivio vuoto: pagina giusta ma senza tabella
            if "Registrazioni" in page.title():
                return []
            raise RuntimeError(f"archivio non raggiunto ({page.title()[:60]})") from None
        corso = page.locator(".ElementInfoCard2 b").first.inner_text() if page.locator(".ElementInfoCard2 b").count() else ""
        # vista "tutte" (la tabella è paginata a 10)
        all_link = page.eval_on_selector_all(
            "a.paginator_link", "els => (els.find(e => e.href.includes('action=plen_0')) || {}).href || null")
        if all_link:
            page.goto(all_link, timeout=60_000)
            page.wait_for_selector("tbody.TableDati-tbody", timeout=45_000)
        rows = [r for r in page.evaluate(_rows_js()) if r.get("preview")]
        log(f"archivio: {corso[:60]} · {len(rows)} registrazioni")

        out = []
        for i, r in enumerate(rows, 1):
            webex = _resolve(ctx, page, urljoin(page.url, r["preview"]))
            if not webex:
                log(f"archivio: riga {i} ({r['data']}) senza link Webex, saltata")
                continue
            out.append({"webex": webex, "data": r["data"], "forma": r["forma"], "argomento": r["argomento"]})
        return out


def _resolve(ctx, page, preview: str) -> str | None:
    """'Riproduci' è un redirect HTTP verso Webex: si legge la destinazione senza aprire il player."""
    try:
        resp = ctx.request.get(preview, max_redirects=0, timeout=30_000)
        if m := _WEBEX_RX.search(resp.headers.get("location", "")):
            return m.group(0)
    except Exception:  # noqa: BLE001
        pass
    # ripiego: navigazione vera (alcune versioni rimandano con JavaScript)
    p = ctx.new_page()
    try:
        p.goto(preview, timeout=60_000, wait_until="commit")
        p.wait_for_url(_WEBEX_RX, timeout=30_000)
        m = _WEBEX_RX.search(p.url)
        return m.group(0) if m else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        p.close()


def write_links(rows: list[dict], path: Path, source: str) -> None:
    """File nello stesso formato arricchito di link.txt (leggibile da download.read_links_file)."""
    lines = [f"# Registrazioni raccolte automaticamente dall'archivio ({source})",
             "# formato: link<TAB>data archivio<TAB>forma didattica<TAB>argomento — si può anche modificare a mano"]
    lines += [f"{r['webex']}\t{r['data']}\t{r['forma']}\t{r['argomento']}" for r in rows]
    atomic_write_text(path, "\n".join(lines) + "\n")
