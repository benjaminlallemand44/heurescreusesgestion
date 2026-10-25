from datetime import date

import pandas as pd
import pytest

from hc import releves
from hc.tarifs import Grille, HistoriqueTarifs

HISTO = HistoriqueTarifs([Grille(date(2025, 1, 1), 15.0, 0.30, 0.20, 14.0, 0.25)])


def exemple():
    df = releves.charger(__import__("pathlib").Path("/inexistant.csv"))
    df = releves.ajouter(df, "2026-10-20 20:00", 12450, 8320)
    return releves.ajouter(df, "2026-10-21 20:00", 12458, 8323)


def test_periodes():
    per = releves.periodes(exemple(), HISTO)
    assert len(per) == 1
    p = per.iloc[0]
    assert p["kwh_hc"] == 8 and p["kwh_hp"] == 3
    assert p["jours"] == pytest.approx(1)
    assert p["part_hc"] == pytest.approx(8 / 11)
    assert p["cout_energie"] == pytest.approx(3 * 0.30 + 8 * 0.20)


def test_verification_index_decroissant():
    erreurs = releves.verifier_nouveau(exemple(), "2026-10-22 20:00", 12400, 8330)
    assert any("HC" in e for e in erreurs)
    assert releves.verifier_nouveau(exemple(), "2026-10-22 20:00", 12460, 8330) == []
    assert releves.verifier_nouveau(exemple(), "2026-10-21 20:00", 12460, 8330)


def test_bilan_journalier_repartit_sur_les_jours():
    bilan = releves.bilan_journalier(exemple(), HISTO)
    # 20h → 20h : 4 h le 20, 20 h le 21
    assert list(bilan["jour"]) == [date(2026, 10, 20), date(2026, 10, 21)]
    assert bilan["kwh_total"].sum() == pytest.approx(11)
    assert bilan["kwh_total"].iloc[0] == pytest.approx(11 * 4 / 24)
    assert bilan["abonnement_jour"].sum() == pytest.approx(15.0 / (365.25 / 12))


def test_sauvegarde(tmp_path):
    chemin = tmp_path / "releves.csv"
    releves.sauvegarder(exemple(), chemin)
    relu = releves.charger(chemin)
    pd.testing.assert_frame_equal(relu, exemple(), check_dtype=False)
