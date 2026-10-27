from datetime import date

import pandas as pd
import pytest

from hc import calculs
from hc.demo import courbe_demo
from hc.tarifs import Grille, HistoriqueTarifs, Offre, parse_plages

GRILLE = Grille(date(2025, 1, 1), 15.0, 0.30, 0.20, 14.0, 0.25)
HISTO = HistoriqueTarifs([GRILLE])
PLAGES = parse_plages("22:00-06:00")


def courbe_constante(puissance_w=1000, jours=1):
    dates = pd.date_range("2026-10-01 00:30", periods=48 * jours, freq="30min")
    return pd.DataFrame({"date": dates, "puissance_w": puissance_w})


def test_energie_par_intervalle():
    e = calculs.energie_par_intervalle(courbe_constante(1000))
    assert e["kwh"].sum() == pytest.approx(24)  # 1 kW pendant 24 h
    assert e["debut"].iloc[0] == pd.Timestamp("2026-10-01 00:00")


def test_bilan_journalier_hp_hc():
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_constante(1000)), PLAGES)
    bilan = calculs.bilan_journalier(classe, HISTO)
    assert len(bilan) == 1
    ligne = bilan.iloc[0]
    assert ligne["kwh_hc"] == pytest.approx(8)
    assert ligne["kwh_hp"] == pytest.approx(16)
    assert ligne["part_hc"] == pytest.approx(1 / 3)
    assert ligne["cout_energie"] == pytest.approx(16 * 0.30 + 8 * 0.20)
    assert ligne["puissance_max_w"] == 1000


def test_cout_suit_l_historique_des_prix():
    nouvelle = Grille(date(2026, 10, 2), 15.0, 0.40, 0.30, 14.0, 0.35)
    histo = HistoriqueTarifs([GRILLE, nouvelle])
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_constante(1000, jours=2)), PLAGES)
    bilan = calculs.bilan_journalier(classe, histo)
    assert bilan["cout_energie"].iloc[0] == pytest.approx(16 * 0.30 + 8 * 0.20)
    assert bilan["cout_energie"].iloc[1] == pytest.approx(16 * 0.40 + 8 * 0.30)


def test_talon_et_profil_demo():
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_demo(14)), PLAGES)
    assert 100 < calculs.talon_w(classe) < 200
    profil = calculs.profil_horaire(classe)
    assert profil["heure"].nunique() == 48


def test_pics_heures_pleines_detecte_le_lave_linge():
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_demo(14)), PLAGES)
    pics = calculs.pics_heures_pleines(classe, 1500, 0.10)
    assert set(pics["heure"]) >= {"18:00", "18:30", "19:00"}
    assert (pics["gain_possible"] > 0).all()


def test_cout_offre_base_vs_hc():
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_constante(1000, jours=2)), PLAGES)
    hc = calculs.cout_offre(classe, Offre("HC", 15.0, 0.30, 0.20, PLAGES))
    base = calculs.cout_offre(classe, Offre("Base", 14.0, 0.25))
    assert hc["energie"] == pytest.approx(2 * (16 * 0.30 + 8 * 0.20))
    assert base["energie"] == pytest.approx(48 * 0.25)
    assert hc["abonnement"] == pytest.approx(15.0 * 2 / calculs.JOURS_PAR_MOIS)


def test_part_hc_rentable():
    x = calculs.part_hc_rentable(15.0, 0.30, 0.20, 14.0, 0.25, kwh_par_an=3000)
    # à ce seuil, les deux options coûtent pareil
    cout_hc = 15 * 12 + 3000 * (0.30 * (1 - x) + 0.20 * x)
    cout_base = 14 * 12 + 3000 * 0.25
    assert cout_hc == pytest.approx(cout_base)
    assert calculs.part_hc_rentable(15.0, 0.30, 0.29, 14.0, 0.25, 3000) is None


def test_estimation_facture():
    bilan = pd.DataFrame({
        "jour": [date(2026, 10, d) for d in range(1, 11)],
        "kwh_total": 10.0, "cout_energie": 2.0, "abonnement_jour": 0.5,
    })
    estim = calculs.estimation_facture(bilan, date(2026, 10, 10))
    assert estim["mois_deja_consomme"] == pytest.approx(25)
    assert estim["mois_projection"] == pytest.approx(31 * 2.5)
    assert estim["annee_projection"] == pytest.approx(365 * 2.5)


def test_plages_hors_demi_heure_reparties_au_prorata():
    plages = parse_plages("00:56-06:56; 14:56-16:56")
    classe = calculs.classer_hp_hc(calculs.energie_par_intervalle(courbe_constante(1000)), plages)
    assert classe.loc[classe["periode"] == "HC", "kwh"].sum() == pytest.approx(8)
    assert classe["kwh"].sum() == pytest.approx(24)
    # 06:30-07:00 : 26 min en HC, 4 min en HP
    tranche = classe[classe["intervalle"] == pd.Timestamp("2026-10-01 06:30")]
    assert tranche.set_index("periode")["kwh"].to_dict() == pytest.approx({"HC": 0.5 * 26 / 30, "HP": 0.5 * 4 / 30})
    # le profil redonne la puissance moyenne de chaque demi-heure
    profil = calculs.profil_horaire(classe)
    assert profil.groupby("heure")["puissance_w"].sum().tolist() == pytest.approx([1000] * 48)
