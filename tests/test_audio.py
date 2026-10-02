import shutil
import subprocess

import pytest

from sbob.core.layout import Layout
from sbob.steps.audio import run
from sbob.steps.base import StepContext

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg assente")


def _make_video(path):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-shortest", str(path)],
                   check=True, capture_output=True)


def test_audio_converts_and_skips(settings):
    c = settings.corso("prova")
    lay = Layout.of(c)
    lay.ensure("video")
    _make_video(lay.video / "2025-09-17_prova_lez01.mp4")
    (lay.video / "rotto.mp4").write_text("non è un video")

    rep = run(StepContext(settings, c, quiet=True))
    assert rep.done == ["2025-09-17_prova_lez01"]
    assert [f.item for f in rep.failed] == ["rotto"]
    assert (lay.audio / "2025-09-17_prova_lez01.aac").stat().st_size > 0
    assert not list(lay.audio.glob(".*"))                       # nessun temporaneo residuo

    rep2 = run(StepContext(settings, c, quiet=True))
    assert rep2.skipped == ["2025-09-17_prova_lez01"] and rep2.done == []

    dry = run(StepContext(settings, c, force=True, dry_run=True, quiet=True))
    assert dry.done == ["2025-09-17_prova_lez01", "rotto"] and dry.dry_run
