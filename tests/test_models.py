from sbob.core import models

AVAILABLE = ["gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
             "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.8-flash-tts", "gemini-3.5-transcribe", "gemini-flash-latest",
             "gemini-3.1-flash-image"]


def cfg(*rows):
    return [{"ruolo": r, "modello": m, "tipo": t} for r, m, t in rows]


def test_family_ignores_tts_image_and_special():
    assert models.family("gemini-3.6-flash") == ((3, 6), "flash", False)
    assert models.family("models/gemini-3.5-flash-lite") == ((3, 5), "flash-lite", False)
    assert models.family("gemini-3-flash-preview") == ((3,), "flash", True)
    for n in ("gemini-3.8-flash-tts", "gemini-3.1-flash-image", "gemini-3.5-transcribe", "gemini-flash-latest"):
        assert models.family(n) is None


def test_newer_versions_are_reported_but_never_applied():
    res = models.analyze(cfg(("notes", "gemini-3.6-flash", "principale")), AVAILABLE, None)
    assert res["ruoli"][0]["piu_nuovi"] == ["gemini-3.7-flash", "gemini-3.8-flash"]
    assert any(w.startswith("Flash: il più nuovo che usi è 3.6") and "3.8 Flash" in w for w in res["avvisi"]) and res["nota"]


def test_flash_lite_is_compared_only_with_flash_lite():
    res = models.analyze(cfg(("mappa", "gemini-3.5-flash-lite", "principale")), AVAILABLE, None)
    assert res["ruoli"][0]["piu_nuovi"] == []                                 # i flash più nuovi non c'entrano


def test_retired_model_and_preview_are_flagged():
    res = models.analyze(cfg(("pdf", "gemini-2.9-flash", "principale"), ("trascrizione", "gemini-3-flash-preview", "principale")),
                         AVAILABLE, None)
    assert not res["ruoli"][0]["disponibile"] and any("non è più nell'elenco" in w for w in res["avvisi"])
    assert any("è una preview" in w and "gemini-3.5-flash" in w for w in res["avvisi"])


def test_new_since_last_check_and_dedicated_transcription_model():
    res = models.analyze(cfg(("trascrizione", "gemini-3.5-flash", "principale")), AVAILABLE, ["gemini-3.5-flash", "gemini-3.6-flash"])
    assert "gemini-3.8-flash" in res["nuovi_dall_ultimo_controllo"]
    assert any("dedicato alla trascrizione" in w for w in res["avvisi"]) and any(w.startswith("Nuovi dall'ultimo") for w in res["avvisi"])
    assert models.analyze(cfg(("trascrizione", "gemini-3.5-transcribe", "principale")), AVAILABLE, None)["avvisi"] == []


def test_role_models_include_reserves(settings):
    rows = models.role_models(settings)
    assert {"ruolo": "notes", "modello": "gemini-3.6-flash", "tipo": "principale"} in rows or any(r["tipo"] == "riserva" for r in rows)


def test_deliberate_spread_across_versions_is_not_a_false_alarm():
    conf = cfg(("notes", "gemini-3.8-flash", "principale"), ("refiner", "gemini-3.5-flash-lite", "principale"),
               ("mappa", "gemini-3.1-flash-lite", "principale"), ("pdf", "gemini-3.6-flash", "principale"))
    res = models.analyze(conf, AVAILABLE, None)
    assert not any(w.startswith(("Flash:", "Flash Lite:")) for w in res["avvisi"])        # 3.8 è già usato; il Lite più nuovo pure
