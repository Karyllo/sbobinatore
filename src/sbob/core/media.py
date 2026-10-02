"""Helper ffmpeg/ffprobe condivisi (durata, conversione, split)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        lines = [l for l in res.stderr.strip().splitlines() if l.strip()]
        raise RuntimeError(f"{cmd[0]} fallito: {lines[-1] if lines else 'errore sconosciuto'}")
    return res


def duration_seconds(path: Path) -> float:
    """Durata in secondi. Per .aac grezzo (ADTS) ffprobe stima dal bitrate e sbaglia anche di 3×:
    lì si contano i pacchetti (1024 campioni ciascuno), che è esatto."""
    try:
        if path.suffix.lower() == ".aac":
            res = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets,sample_rate", "-print_format", "json", str(path)])
            st = json.loads(res.stdout)["streams"][0]
            return int(st["nb_read_packets"]) * 1024 / int(st["sample_rate"])
        res = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)])
        return float(json.loads(res.stdout)["format"].get("duration", 0))
    except Exception:  # noqa: BLE001
        return 0.0


def to_m4a(src: Path, dst_dir: Path) -> Path:
    dst = dst_dir / f"{src.stem}.m4a"
    _run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-q:a", "2", str(dst)])
    return dst


def split_audio(src: Path, dst_dir: Path, segment_seconds: int) -> list[Path]:
    """Divide senza ricodificare in segmenti da ~segment_seconds. Restituisce i file in ordine."""
    pattern = dst_dir / f"{src.stem}.part%03d{src.suffix}"
    _run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", "-c", "copy", "-f", "segment",
          "-segment_time", str(segment_seconds), "-reset_timestamps", "1", str(pattern)])
    return sorted(dst_dir.glob(f"{src.stem}.part*{src.suffix}"))
