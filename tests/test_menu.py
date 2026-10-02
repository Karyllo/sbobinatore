from sbob.core.layout import Layout
from sbob.core.status import course_status
from sbob.menu import suggest_steps, summary


def test_suggest_steps_follows_pending_work(settings):
    c = settings.corso("prova")
    assert suggest_steps(course_status(c), c) == {"download": True, "audio": False, "trascrivi": False, "appunti": False}
    lay = Layout.of(c)
    lay.ensure("video", "audio")
    (lay.video / "2025-09-17_prova_lez01.mp4").write_text("x")
    (lay.audio / "2025-09-18_prova_lez02.aac").write_text("x")
    st = course_status(c)
    # lez01 ha solo il video → serve l'audio; lez02 ha solo l'audio → serve la trascrizione
    assert suggest_steps(st, c) == {"download": False, "audio": True, "trascrivi": True, "appunti": False}
    assert "2 lezioni" in summary(st)
