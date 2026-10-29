from datetime import date, datetime

import pandas as pd
import pytest

from hc import appareils, apprentissage, calculs, optimisation
from hc import journal as jr
from hc.tarifs import Grille, TarifsTempo, parse_plages

INVENTAIRE = appareils.CATALOGUE[appareils.CATALOGUE["nom"].isin(
    ["Lave-linge", "Plaques de cuisson", "Réfrigérateur-congélateur", "Ballon d'eau chaude", "Radiateur séjour"])]
GRILLE = Grille(date(2025, 1, 1), 15.0, 0.30, 0.20, 14.0, 0.25)
PLAGES = parse_plages("00:56-06:56; 14:56-16:56")


def courbe(jours=3, fond=200, ajouts=()):
    """Courbe à 30 min : fond constant + (début, fin, watts) ajoutés."""
    dates = pd.date_range("2026-10-20 00:30", periods=48 * jours, freq="30min")
    p = pd.Series(float(fond), index=dates)
    for d, f, w in ajouts:
        # date = fin de l'intervalle : un intervalle ]fin-30min, fin] est concerné si son début est dans [d, f[
        debuts = dates - pd.Timedelta(minutes=30)
        p[(debuts >= pd.Timestamp(d)) & (debuts < pd.Timestamp(f))] += w
    return calculs.energie_par_intervalle(pd.DataFrame({"date": dates, "puissance_w": p.values}))


def journal(*utilisations):
    return jr.normaliser(pd.DataFrame([jr.nouvelle(a, pd.Timestamp(d), pd.Timestamp(f)) for a, d, f in utilisations]))


def test_observation_mesure_l_excedent():
    e = courbe(ajouts=[("2026-10-20 19:00", "2026-10-20 21:00", 450)])
    j = journal(("Lave-linge", "2026-10-20 19:00", "2026-10-20 21:00"))
    obs = apprentissage.observations(e, j, INVENTAIRE)
    assert len(obs) == 1
    assert obs.iloc[0]["kwh_mesure"] == pytest.approx(0.9)
    assert obs.iloc[0]["puissance_mesuree_w"] == pytest.approx(450)


def test_utilisations_simultanees_partagees():
    e = courbe(ajouts=[("2026-10-20 19:00", "2026-10-20 20:00", 2450)])
    j = journal(("Lave-linge", "2026-10-20 19:00", "2026-10-20 20:00"),
                ("Plaques de cuisson", "2026-10-20 19:00", "2026-10-20 20:00"))
    obs = apprentissage.observations(e, j, INVENTAIRE).set_index("appareil")
    assert obs["kwh_mesure"].sum() == pytest.approx(2.45)
    # partage au prorata des puissances déclarées : 450 W et 2000 W
    assert obs.loc["Plaques de cuisson", "kwh_mesure"] == pytest.approx(2.45 * 2000 / 2450)


def test_hors_courbe_ignoree():
    e = courbe()
    j = journal(("Lave-linge", "2026-11-20 19:00", "2026-11-20 21:00"))
    assert apprentissage.observations(e, j, INVENTAIRE).empty


def test_apprendre_et_suggerer():
    ajouts = [(f"2026-10-2{i} 19:00", f"2026-10-2{i} 21:00", 600) for i in range(3)]
    e = courbe(ajouts=ajouts)
    j = journal(*[("Lave-linge", d, f) for d, f, _ in ajouts[:2]])
    obs = apprentissage.observations(e, j, INVENTAIRE)
    appris = apprentissage.apprendre(obs, INVENTAIRE).set_index("appareil")
    assert appris.loc["Lave-linge", "n"] == 2
    assert appris.loc["Lave-linge", "kwh_appris"] == pytest.approx(1.2)
    assert appris.loc["Lave-linge", "kwh_declare"] == pytest.approx(0.9)
    # le 3e lavage, non annoté, est reconnu grâce à l'apprentissage
    sugg = apprentissage.suggestions(e, j, INVENTAIRE, appris.reset_index())
    assert len(sugg) == 1
    s = sugg.iloc[0]
    assert s["debut"] == pd.Timestamp("2026-10-22 19:00") and s["appareil"] == "Lave-linge"
    assert s["d_apres"] == "l'apprentissage"


def test_repartition():
    e = courbe(jours=1, ajouts=[("2026-10-20 19:00", "2026-10-20 21:00", 450)])
    j = journal(("Lave-linge", "2026-10-20 19:00", "2026-10-20 21:00"))
    obs = apprentissage.observations(e, j, INVENTAIRE)
    rep = apprentissage.repartition(e, obs, INVENTAIRE).set_index("poste")
    assert rep["kwh"].sum() == pytest.approx(e["kwh"].sum())
    assert rep.loc["Lave-linge", "kwh"] == pytest.approx(0.9)
    assert rep.loc["Réfrigérateur-congélateur", "kwh"] == pytest.approx(35 * 24 / 1000)


def test_journal_lancer_et_en_cours():
    inv = appareils.normaliser(INVENTAIRE).set_index("nom", drop=False)
    u = jr.lancer(inv.loc["Lave-linge"], datetime(2026, 10, 20, 19, 0))
    assert u["fin"] == pd.Timestamp("2026-10-20 21:00")
    p = jr.lancer(inv.loc["Plaques de cuisson"], datetime(2026, 10, 20, 19, 0))
    assert pd.isna(p["fin"])
    j = jr.normaliser(pd.DataFrame([u, p]))
    assert list(jr.en_cours(j, datetime(2026, 10, 20, 19, 30))["appareil"]) == ["Plaques de cuisson"]
    fins = jr.fins_effectives(j, INVENTAIRE, datetime(2026, 10, 20, 19, 30))
    assert list(fins) == [pd.Timestamp("2026-10-20 21:00"), pd.Timestamp("2026-10-20 19:30")]
    # oubliée plus de 12 h : durée typique
    fins = jr.fins_effectives(j, INVENTAIRE, datetime(2026, 10, 22, 8, 0))
    assert fins.iloc[1] == pd.Timestamp("2026-10-20 19:45")
    with pytest.raises(ValueError):
        jr.nouvelle("Lave-linge", datetime(2026, 10, 20, 19), datetime(2026, 10, 20, 18))


def test_part_heures_pleines():
    assert optimisation.part_heures_pleines("2026-10-20 14:00", "2026-10-20 16:00", PLAGES) == pytest.approx(56 / 120)
    assert optimisation.part_heures_pleines("2026-10-20 01:00", "2026-10-20 03:00", PLAGES) == 0
    assert optimisation.part_heures_pleines("2026-10-20 19:00", "2026-10-20 21:00", PLAGES) == 1


def test_conseils_decaler_et_chauffage():
    maintenant = datetime(2026, 10, 27, 12, 0)
    j = journal(*[("Lave-linge", f"2026-10-2{i} 19:00", f"2026-10-2{i} 21:00") for i in range(7)])
    c = optimisation.conseils(INVENTAIRE, j, pd.DataFrame(columns=["id", "kwh_mesure"]),
                              apprentissage.apprendre(pd.DataFrame(), INVENTAIRE), PLAGES, GRILLE,
                              talon_w=150, a_l_instant=maintenant)
    titres = [x["titre"] for x in c]
    decaler = next(x for x in c if x["titre"] == "Décaler « Lave-linge » en heures creuses")
    # 7 lavages de 0,9 kWh en 7 jours suivis, tous en HP
    assert decaler["gain_an"] == pytest.approx(7 * 0.9 * 365 / 7 * 0.10)
    assert "00:56" in decaler["detail"]
    assert "Baisser le chauffage de 1 °C" in titres
    assert "Programmer « Ballon d'eau chaude » en heures creuses" in titres
    # talon 150 W > 35 W déclarés : consommation de fond à traquer
    assert any(t.startswith("Traquer") for t in titres)
    assert [x["gain_an"] for x in c] == sorted((x["gain_an"] for x in c), reverse=True)


def test_tempo_couleurs_et_cout():
    # 10 jours de janvier, lun.-ven. éligibles rouges : les plus gros jours deviennent rouges
    dates = pd.date_range("2027-01-04 00:30", periods=48 * 10, freq="30min")
    puissance = [3000 if d.day == 5 else 500 for d in dates - pd.Timedelta(minutes=30)]
    e = calculs.energie_par_intervalle(pd.DataFrame({"date": dates, "puissance_w": puissance}))
    tempo = TarifsTempo()
    res = calculs.cout_tempo(e, tempo)
    assert sum(res["jours_couleurs"].values()) >= 10
    assert res["jours_couleurs"].get("rouge", 0) >= 1
    assert res["total"] > 0 and res["kwh_hp"] + res["kwh_hc"] == pytest.approx(e["kwh"].sum())
    jours = pd.DataFrame({"jour_tempo": pd.date_range("2027-07-05", periods=5).date, "kwh": [10, 20, 30, 40, 50]})
    assert set(calculs.couleurs_tempo(jours, tempo)) == {"bleu"}  # pas de rouge ni blanc en juillet


def test_suggestion_departage_par_la_puissance():
    # 2 h 30 à ~1 800 W la nuit : énergie proche d'un radiateur de 1 000 W × 4 h, mais puissance de ballon
    e = courbe(jours=1, ajouts=[("2026-10-20 01:00", "2026-10-20 03:30", 1800)])
    sugg = apprentissage.suggestions(e, jr.vide(), INVENTAIRE, apprentissage.apprendre(pd.DataFrame(), INVENTAIRE))
    assert sugg.iloc[0]["appareil"] == "Ballon d'eau chaude"


def test_pas_de_suggestion_sans_ressemblance():
    e = courbe(jours=1, ajouts=[("2026-10-20 10:00", "2026-10-20 10:30", 5000)])
    sugg = apprentissage.suggestions(e, jr.vide(), INVENTAIRE, apprentissage.apprendre(pd.DataFrame(), INVENTAIRE))
    assert len(sugg) == 1 and sugg.iloc[0]["appareil"] is None


def test_journee_sans_pic():
    e = courbe(jours=1)  # consommation de fond uniquement
    vide = apprentissage.apprendre(pd.DataFrame(), INVENTAIRE)
    assert apprentissage.pics_non_annotes(e, jr.vide(), INVENTAIRE).empty
    assert apprentissage.suggestions(e, jr.vide(), INVENTAIRE, vide).empty
    # tous les pics déjà annotés : rien à proposer non plus
    e = courbe(jours=1, ajouts=[("2026-10-20 19:00", "2026-10-20 21:00", 450)])
    j = journal(("Lave-linge", "2026-10-20 19:00", "2026-10-20 21:00"))
    assert apprentissage.suggestions(e, j, INVENTAIRE, vide).empty


def test_rechauffe_du_ballon_au_debut_des_heures_creuses():
    # Courbe horaire comme l'export Enedis : petites rechauffes de ~0,5-0,7 kWh vers 1 h et 15 h
    dates = pd.date_range("2026-10-21 01:00", periods=24, freq="h")
    p = [490 if d.hour == 2 else 670 if d.hour == 16 else 20 for d in dates]
    e = calculs.energie_par_intervalle(pd.DataFrame({"date": dates, "puissance_w": p}))
    vide = apprentissage.apprendre(pd.DataFrame(), INVENTAIRE)
    sugg = apprentissage.suggestions(e, jr.vide(), INVENTAIRE, vide, plages=PLAGES)
    assert list(sugg["appareil"]) == ["Ballon d'eau chaude", "Ballon d'eau chaude"]
    assert set(sugg["d_apres"]) == {"le début des heures creuses"}
    # sans les plages, pas d'indice horaire
    assert "Ballon d'eau chaude" not in list(apprentissage.suggestions(e, jr.vide(), INVENTAIRE, vide)["appareil"])
