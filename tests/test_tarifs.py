from datetime import date, time

import pandas as pd
import pytest

from hc.tarifs import GRILLE_DEFAUT, Grille, HistoriqueTarifs, Plage, est_heure_creuse, parse_plages


def test_plage_a_cheval_sur_minuit():
    p = Plage(time(22, 0), time(6, 0))
    assert p.contient(time(23, 30))
    assert p.contient(time(0, 0))
    assert p.contient(time(5, 59))
    assert not p.contient(time(6, 0))
    assert not p.contient(time(12, 0))
    assert p.duree_heures() == 8


def test_parse_plusieurs_plages():
    plages = parse_plages("01:30-07:30; 12:30-14:30")
    assert [str(p) for p in plages] == ["01:30-07:30", "12:30-14:30"]
    assert sum(p.duree_heures() for p in plages) == 8
    assert est_heure_creuse(time(13, 0), plages)
    assert not est_heure_creuse(time(8, 0), plages)


@pytest.mark.parametrize("texte", ["", "22h-6h", "22:00", "10:00-10:00"])
def test_parse_plages_invalides(texte):
    with pytest.raises(ValueError):
        parse_plages(texte)


def test_historique_choisit_la_grille_en_vigueur():
    nouvelle = Grille(date(2025, 8, 1), 16.0, 0.28, 0.21, 15.6, 0.26)
    h = HistoriqueTarifs([nouvelle, GRILLE_DEFAUT])
    assert h.grille_a(date(2025, 3, 1)) is GRILLE_DEFAUT
    assert h.grille_a(date(2025, 8, 1)) is nouvelle
    assert h.grille_a(date(2024, 1, 1)) is GRILLE_DEFAUT
    assert h.actuelle() is nouvelle


def test_historique_aller_retour_dataframe():
    h = HistoriqueTarifs([GRILLE_DEFAUT])
    df = h.vers_dataframe()
    df["date_debut"] = pd.to_datetime(df["date_debut"])
    assert HistoriqueTarifs.depuis_dataframe(df).grilles == h.grilles
