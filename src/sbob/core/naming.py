"""Unica regola di nome per le lezioni.

Formato canonico dello stem:  YYYY-MM-DD_<slug>_<tipo><NN>[_<parte>]
  es. 2025-10-29_real_analysis_lez10_2

Lo stem è la chiave che lega video → audio → trascrizione → appunti:
  video/<stem>.mp4, audio/<stem>.aac, trascrizioni/<stem>.md, appunti/<stem>_appunti.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

TIPI = ("lez", "ese", "lab", "sem", "tde")
NOTES_SUFFIX = "_appunti"

_CANON = re.compile(
    r"^(?P<data>\d{4}-\d{2}-\d{2})_(?P<slug>.+?)_(?P<tipo>lez|ese|lab|sem|tde)(?P<num>\d{2,})(?:_(?P<parte>\d+))?$"
)
# Formato dei vecchi script: 2025_09_17-real_analysis-lez01[_2]
_LEGACY = re.compile(
    r"^(?P<data>\d{4}_\d{2}_\d{2})-(?P<slug>.+?)-(?P<tipo>lez|ese|lab|sem|tde)(?P<num>\d{2,})(?:_(?P<parte>\d+))?$"
)
# Nome prodotto da prd: "2025-09-17 17-09.mp4"
_PRD = re.compile(r"^(?P<data>\d{4}-\d{2}-\d{2}) (?P<ora>\d{2}-\d{2})$")


@dataclass(frozen=True, order=True)
class LessonName:
    data: date
    slug: str
    tipo: str
    num: int
    parte: int | None = None

    @property
    def stem(self) -> str:
        s = f"{self.data.isoformat()}_{self.slug}_{self.tipo}{self.num:02d}"
        return f"{s}_{self.parte}" if self.parte else s

    def __str__(self) -> str:
        return self.stem


def slugify(text: str) -> str:
    s = re.sub(r"[^\w]+", "_", text.strip().lower(), flags=re.UNICODE)
    return s.strip("_")


def parse(stem: str) -> LessonName | None:
    """Riconosce sia il formato canonico sia quello legacy. None se non è un nome di lezione."""
    if stem.endswith(NOTES_SUFFIX):
        stem = stem[: -len(NOTES_SUFFIX)]
    for rx, fmt in ((_CANON, "%Y-%m-%d"), (_LEGACY, "%Y_%m_%d")):
        if m := rx.match(stem):
            return LessonName(
                data=datetime.strptime(m["data"], fmt).date(),
                slug=m["slug"],
                tipo=m["tipo"],
                num=int(m["num"]),
                parte=int(m["parte"]) if m["parte"] else None,
            )
    return None


def parse_prd(stem: str) -> datetime | None:
    """Data/ora dal nome file prodotto dal downloader (prd)."""
    if m := _PRD.match(stem):
        return datetime.strptime(f"{m['data']} {m['ora']}", "%Y-%m-%d %H-%M")
    return None


def next_numbers(existing: list[LessonName], new_dates: list[date], slug: str, tipo: str = "lez") -> list[LessonName]:
    """Assegna i numeri alle nuove lezioni di un tipo, continuando dal massimo esistente.

    Le date nuove vengono ordinate. Più registrazioni nello stesso giorno ricevono numeri consecutivi:
    lo split in parti (_1, _2) resta una scelta manuale.
    """
    start = max((n.num for n in existing if n.tipo == tipo), default=0)
    return [LessonName(d, slug, tipo, start + i) for i, d in enumerate(sorted(new_dates), 1)]
