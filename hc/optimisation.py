"""Conseils d'économies, classés par gain annuel estimé.

Les gains s'appuient, par ordre de préférence, sur vos utilisations mesurées
(journal + courbe), sur les valeurs apprises, puis sur vos réglages.
"""

from datetime import timedelta

import pandas as pd

from hc import appareils as app
from hc import journal as jr
from hc.calculs import _bornes_plages
from hc.tarifs import Grille, Plage, est_heure_creuse

FENETRE_JOURNAL = timedelta(days=90)
GAIN_MIN = 1.0  # € / an : en dessous, le conseil n'est pas affiché


def part_heures_pleines(debut, fin, plages: list[Plage]) -> float:
    """Part du temps entre debut et fin passée en heures pleines."""
    debut, fin = pd.Timestamp(debut), pd.Timestamp(fin)
    duree = (fin - debut).total_seconds()
    if duree <= 0:
        return 0.0
    coupures = [debut, *_bornes_plages(debut, fin, plages), fin]
    # une utilisation de plus d'un jour : on se contente des bornes des deux jours extrêmes
    hp = sum((f - d).total_seconds() for d, f in zip(coupures, coupures[1:])
             if not est_heure_creuse(d.time(), plages))
    return hp / duree


def prix_moyen(grille: Grille, plages: list[Plage]) -> float:
    heures_hc = sum(p.duree_heures() for p in plages)
    return (grille.prix_hp * (24 - heures_hc) + grille.prix_hc * heures_hc) / 24


def _conseil(titre, detail, gain, fiabilite, categorie):
    return {"titre": titre, "detail": detail, "gain_an": gain, "fiabilite": fiabilite, "categorie": categorie}


def conseils(appareils: pd.DataFrame, journal: pd.DataFrame, obs: pd.DataFrame, appris: pd.DataFrame,
             plages: list[Plage], grille: Grille, talon_w: float | None = None, a_l_instant=None) -> list[dict]:
    a_l_instant = pd.Timestamp(a_l_instant or jr.maintenant())
    inv = app.bilan_inventaire(appareils)
    ecart = grille.prix_hp - grille.prix_hc
    p_moyen = prix_moyen(grille, plages)
    kwh_mesure = obs.set_index("id")["kwh_mesure"].to_dict() if not obs.empty else {}
    kwh_retenu = appris.set_index("appareil")["kwh_retenu"].to_dict() if not appris.empty else {}
    resultat = []

    recents = journal[journal["debut"] >= a_l_instant - FENETRE_JOURNAL].copy()
    if not recents.empty:
        recents["fin_eff"] = jr.fins_effectives(recents, appareils, a_l_instant)
        jours_suivis = max((a_l_instant - recents["debut"].min()).days, 7)

    # 1. Appareils déplaçables : décaler en heures creuses
    for _, a in inv.iterrows():
        if not a["deplacable"] or a["usage"] == "permanent":
            continue
        creneau = app.meilleur_creneau(plages, app.duree_typique(a))
        quand = (f"lancez-le à {creneau.debut:%H:%M} (fin avant {creneau.fin:%H:%M})" if creneau
                 else "lancez-le au début de votre plus longue plage creuse")
        utilisations = recents[recents["appareil"] == a["nom"]] if not recents.empty else recents
        if len(utilisations):
            kwh_hp = 0.0
            mesures = 0
            for u in utilisations.itertuples():
                kwh = kwh_mesure.get(u.id)
                mesures += kwh is not None
                if kwh is None:
                    kwh = kwh_retenu.get(a["nom"], a["kwh_utilisation"])
                kwh_hp += kwh * part_heures_pleines(u.debut, u.fin_eff, plages)
            gain = kwh_hp * 365 / jours_suivis * ecart
            n_hp = sum(part_heures_pleines(u.debut, u.fin_eff, plages) > 0.5 for u in utilisations.itertuples())
            if gain >= GAIN_MIN and n_hp:
                resultat.append(_conseil(
                    f"Décaler « {a['nom']} » en heures creuses",
                    f"{n_hp} de vos {len(utilisations)} dernières utilisations étaient surtout en heures pleines : "
                    f"{quand}.",
                    gain, "mesurée" if mesures else "estimée", a["categorie"]))
        elif a["utilisations_semaine"] > 0:
            gain = a["kwh_an"] * ecart
            if gain >= GAIN_MIN:
                detail = (f"Si vous l'utilisez aujourd'hui en heures pleines : {quand}. "
                          "Indiquez vos utilisations avec « Je lance… » pour vérifier.")
                if a["categorie"] == "Eau chaude":
                    detail = ("Vérifiez qu'il ne chauffe qu'en heures creuses : contacteur jour/nuit du tableau "
                              "électrique en position « auto ». Indiquez ses chauffes pour le confirmer.")
                resultat.append(_conseil(f"Programmer « {a['nom']} » en heures creuses", detail,
                                         gain, "théorique", a["categorie"]))

    # 2. Chauffage
    kwh_chauffage = inv.loc[inv["categorie"] == "Chauffage", "kwh_an"].sum()
    if kwh_chauffage > 0:
        resultat.append(_conseil(
            "Baisser le chauffage de 1 °C",
            "Chaque degré en moins réduit d'environ 7 % la consommation de chauffage. Repères : 19 °C dans les "
            "pièces de vie, 17 °C dans les chambres.",
            kwh_chauffage * 0.07 * p_moyen, "ordre de grandeur", "Chauffage"))
        resultat.append(_conseil(
            "Programmer le chauffage (nuit et absences)",
            "Abaisser la consigne de 3-4 °C la nuit et pendant vos absences (thermostats programmables ou "
            "têtes connectées) économise souvent 10 % du chauffage.",
            kwh_chauffage * 0.10 * p_moyen, "ordre de grandeur", "Chauffage"))

    # 3. Ballon d'eau chaude : température
    kwh_eau = inv.loc[inv["categorie"] == "Eau chaude", "kwh_an"].sum()
    if kwh_eau > 0:
        resultat.append(_conseil(
            "Régler le ballon à 55 °C",
            "Au-delà, les pertes augmentent sans gain de confort ; en dessous, risque de légionelles.",
            kwh_eau * 0.05 * p_moyen, "ordre de grandeur", "Eau chaude"))

    # 4. Consommation de fond
    permanents = inv[inv["usage"] == "permanent"]
    declare_w = float((permanents["puissance_w"] * permanents["quantite"]).sum())
    if talon_w is not None and talon_w - declare_w > 30:
        inexplique = talon_w - declare_w
        resultat.append(_conseil(
            f"Traquer {inexplique:.0f} W de consommation de fond inexpliquée",
            f"Votre fond mesuré est de {talon_w:.0f} W, vos appareils permanents en expliquent {declare_w:.0f} W. "
            "Pistes : veilles (TV, console, décodeur), multiprises à interrupteur, vieux réfrigérateur, VMC, "
            "appareils oubliés. Ajoutez-les dans vos appareils une fois identifiés.",
            inexplique * 8.76 * p_moyen * 0.5, "estimée", "Fond"))
    veilles = inv[(inv["usage"] == "permanent") & inv["nom"].str.contains("veille", case=False)]
    w_veilles = float((veilles["puissance_w"] * veilles["quantite"]).sum())
    if w_veilles > 0:
        resultat.append(_conseil(
            "Couper les veilles avec une multiprise à interrupteur",
            f"{w_veilles:.0f} W de veilles en continu : les couper la nuit et en journée en supprime la plupart.",
            w_veilles * 0.7 * 8.76 * p_moyen, "estimée", "Multimédia"))

    # 5. Ce que l'apprentissage révèle
    for _, a in appris.iterrows():
        if a["n"] >= 3 and pd.notna(a["kwh_declare"]) and a["kwh_declare"] > 0 \
                and a["kwh_appris"] > 1.5 * a["kwh_declare"]:
            resultat.append(_conseil(
                f"« {a['appareil']} » consomme plus que prévu",
                f"Mesuré : {a['kwh_appris']:.2f} kWh par utilisation, contre {a['kwh_declare']:.2f} kWh d'après vos "
                "réglages. Programme plus chaud ou plus long ? Appareil à détartrer ou à remplacer ?",
                0.0, "mesurée", "Apprentissage"))

    resultat = [c for c in resultat if c["gain_an"] >= GAIN_MIN or c["categorie"] == "Apprentissage"]
    return sorted(resultat, key=lambda c: c["gain_an"], reverse=True)
