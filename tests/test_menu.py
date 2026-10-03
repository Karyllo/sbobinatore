from sbob.core.layout import Layout
from sbob.core.status import course_status
from sbob.menu import suggest_steps, summary


def test_suggest_steps_follows_pending_work(settings):
    c = settings.corso("prova")
    assert suggest_steps(course_status(c), c) == {"materiale": False, "download": True, "audio": False, "trascrivi": False, "appunti": False}
    lay = Layout.of(c)
    lay.ensure("video", "audio")
    (lay.video / "2025-09-17_prova_lez01.mp4").write_text("x")
    (lay.audio / "2025-09-18_prova_lez02.aac").write_text("x")
    st = course_status(c)
    # lez01 ha solo il video → serve l'audio; lez02 ha solo l'audio → serve la trascrizione
    assert suggest_steps(st, c) == {"materiale": False, "download": False, "audio": True, "trascrivi": True, "appunti": False}
    assert "2 lezioni" in summary(st)


def test_home_menu_commands_all_exist():
    """Ogni voce del menu iniziale lancia un comando vero (niente refusi che si scoprono solo davanti all'utente)."""
    from sbob.cli import app
    from sbob.menu import HOME_CHOICES
    names = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    names |= {g.name for g in app.registered_groups}
    for label, target in HOME_CHOICES:
        if isinstance(target, list):
            assert target[0] in names, (label, target)
    assert [t for _, t in HOME_CHOICES][-1] is None and any(t == "corso" for _, t in HOME_CHOICES)


def test_help_text_mentions_only_existing_commands():
    import re

    from sbob.cli import HELP_TEXT, app
    names = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    names |= {g.name for g in app.registered_groups}
    for cmd in re.findall(r"^\s+sbob ([a-z\-]+)", HELP_TEXT, re.M):
        assert cmd in names, cmd
