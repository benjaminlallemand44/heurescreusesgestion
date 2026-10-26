"""Calculs sur la courbe de charge : répartition HP/HC, coûts, indicateurs."""

import calendar
from datetime import date

import pandas as pd

from hc.tarifs import HistoriqueTarifs, Offre, Plage, est_heure_creuse

JOURS_PAR_MOIS = 365.25 / 12


def energie_par_intervalle(courbe: pd.DataFrame) -> pd.DataFrame:
    """Transforme une courbe de puissance en énergie par intervalle.

    `courbe` contient les colonnes `date` (fin de l'intervalle, convention Enedis)
    et `puissance_w` (puissance moyenne sur l'intervalle).
    Retourne `debut`, `fin`, `puissance_w`, `kwh`.
    """
    df = courbe.sort_values("date").reset_index(drop=True)
    df = df.rename(columns={"date": "fin"})
    pas = df["fin"].diff()
    # Le premier intervalle prend le pas le plus fréquent
    pas = pas.fillna(pas.mode().iloc[0] if pas.notna().any() else pd.Timedelta(minutes=30))
    df["debut"] = df["fin"] - pas
    df["kwh"] = df["puissance_w"] * (pas.dt.total_seconds() / 3600) / 1000
    return df[["debut", "fin", "puissance_w", "kwh"]]


def _bornes_plages(debut: pd.Timestamp, fin: pd.Timestamp, plages: list[Plage]) -> list[pd.Timestamp]:
    """Changements HP/HC strictement compris entre debut et fin."""
    bornes = set()
    for jour in {debut.normalize(), fin.normalize()}:
        for p in plages:
            for t in (p.debut, p.fin):
                instant = jour + pd.Timedelta(hours=t.hour, minutes=t.minute)
                if debut < instant < fin:
                    bornes.add(instant)
    return sorted(bornes)


def classer_hp_hc(energie: pd.DataFrame, plages: list[Plage]) -> pd.DataFrame:
    """Ajoute la colonne `periode` ("HC" ou "HP").

    Un intervalle à cheval sur un changement HP/HC (ex. plages en :56 avec une
    courbe à la demi-heure) est découpé, et son énergie répartie au prorata
    du temps passé dans chaque période. Colonnes ajoutées : `intervalle`
    (début de l'intervalle d'origine) et `fraction` (part de l'intervalle).
    """
    lignes = []
    for r in energie.itertuples(index=False):
        coupures = [r.debut, *_bornes_plages(r.debut, r.fin, plages), r.fin]
        duree = (r.fin - r.debut).total_seconds()
        for d, f in zip(coupures, coupures[1:]):
            fraction = (f - d).total_seconds() / duree if duree else 1.0
            lignes.append({
                "debut": d, "fin": f, "puissance_w": r.puissance_w, "kwh": r.kwh * fraction,
                "periode": "HC" if est_heure_creuse(d.time(), plages) else "HP",
                "intervalle": r.debut, "fraction": fraction,
            })
    colonnes = ["debut", "fin", "puissance_w", "kwh", "periode", "intervalle", "fraction"]
    return pd.DataFrame(lignes, columns=colonnes)


def ajouter_cout(df: pd.DataFrame, historique: HistoriqueTarifs, colonne_jour: str) -> pd.DataFrame:
    """Coût de l'énergie et part d'abonnement, avec la grille en vigueur chaque jour.

    `df` contient `kwh_hp`, `kwh_hc` et une colonne de dates `colonne_jour`.
    """
    df = df.copy()
    grilles = [historique.grille_a(pd.Timestamp(j).date()) for j in df[colonne_jour]]
    df["cout_energie"] = [
        g.prix_hp * hp + g.prix_hc * hc for g, hp, hc in zip(grilles, df["kwh_hp"], df["kwh_hc"])
    ]
    df["abonnement_jour"] = [g.abonnement_hc_mensuel / JOURS_PAR_MOIS for g in grilles]
    return df


def bilan_journalier(classe: pd.DataFrame, historique: HistoriqueTarifs) -> pd.DataFrame:
    """kWh HP/HC, part HC, coût et puissance max par jour."""
    df = classe.assign(jour=classe["debut"].dt.date)
    jours = df.pivot_table(
        index="jour", columns="periode", values="kwh", aggfunc="sum", fill_value=0.0
    )
    for col in ("HP", "HC"):
        if col not in jours:
            jours[col] = 0.0
    jours = jours[["HP", "HC"]].rename(columns={"HP": "kwh_hp", "HC": "kwh_hc"})
    jours["kwh_total"] = jours["kwh_hp"] + jours["kwh_hc"]
    jours["part_hc"] = (jours["kwh_hc"] / jours["kwh_total"]).where(jours["kwh_total"] > 0)
    jours["puissance_max_w"] = df.groupby("jour")["puissance_w"].max()
    jours.columns.name = None
    return ajouter_cout(jours.reset_index(), historique, "jour")


def estimation_facture(bilan: pd.DataFrame, aujourd_hui: date, nb_jours_reference: int = 30) -> dict:
    """Projette le mois en cours et l'année à partir des derniers jours connus.

    `bilan` : une ligne par jour avec `jour`, `kwh_total`, `cout_energie`, `abonnement_jour`.
    """
    if bilan.empty:
        return {}
    bilan = bilan.sort_values("jour")
    recents = bilan.tail(nb_jours_reference)
    cout_jour_moyen = (recents["cout_energie"] + recents["abonnement_jour"]).mean()
    kwh_jour_moyen = recents["kwh_total"].mean()

    debut_mois = aujourd_hui.replace(day=1)
    jours_mois = calendar.monthrange(aujourd_hui.year, aujourd_hui.month)[1]
    du_mois = bilan[pd.to_datetime(bilan["jour"]).dt.date >= debut_mois]
    deja = float((du_mois["cout_energie"] + du_mois["abonnement_jour"]).sum())
    jours_restants = jours_mois - len(du_mois)
    return {
        "mois_deja_consomme": deja,
        "mois_projection": deja + max(jours_restants, 0) * cout_jour_moyen,
        "annee_projection": cout_jour_moyen * 365,
        "kwh_annee_projection": kwh_jour_moyen * 365,
        "jours_reference": len(recents),
    }


def talon_w(energie: pd.DataFrame) -> float:
    """Consommation de fond : médiane des minima journaliers de puissance."""
    minima = energie.groupby(energie["debut"].dt.date)["puissance_w"].min()
    return float(minima.median()) if len(minima) else 0.0


def cout_annuel_talon(talon: float, prix_moyen_kwh: float) -> float:
    return talon / 1000 * 24 * 365 * prix_moyen_kwh


def profil_horaire(classe: pd.DataFrame) -> pd.DataFrame:
    """Puissance moyenne par demi-heure de la journée, ventilée HP/HC.

    Une demi-heure à cheval sur un changement de période apparaît dans les
    deux couleurs, au prorata ; les deux parts empilées redonnent la moyenne.
    """
    nb_jours = classe["intervalle"].dt.date.nunique() or 1
    df = classe.assign(
        heure=classe["intervalle"].dt.strftime("%H:%M"),
        puissance_w=classe["puissance_w"] * classe["fraction"],
    )
    res = df.groupby(["heure", "periode"], as_index=False)["puissance_w"].sum()
    res["puissance_w"] /= nb_jours
    return res.sort_values("heure").reset_index(drop=True)


def pics_heures_pleines(classe: pd.DataFrame, seuil_w: float, ecart_prix: float) -> pd.DataFrame:
    """Créneaux horaires où des puissances élevées tombent en heures pleines.

    Retourne, par demi-heure de la journée : nombre de jours concernés, kWh et
    gain possible si cette consommation était déplacée en heures creuses.
    """
    hp = classe[(classe["periode"] == "HP") & (classe["puissance_w"] >= seuil_w)]
    if hp.empty:
        return pd.DataFrame(columns=["heure", "nb_jours", "kwh", "gain_possible"])
    hp = hp.assign(heure=hp["intervalle"].dt.strftime("%H:%M"), jour=hp["intervalle"].dt.date)
    res = hp.groupby("heure").agg(nb_jours=("jour", "nunique"), kwh=("kwh", "sum")).reset_index()
    res["gain_possible"] = res["kwh"] * ecart_prix
    return res.sort_values("kwh", ascending=False).reset_index(drop=True)


def cout_offre(energie: pd.DataFrame, offre: Offre) -> dict:
    """Coût total (abonnement + énergie) d'une offre sur la période de la courbe.

    `energie` : sortie de energie_par_intervalle, classée ici selon les plages de l'offre.
    """
    hc = classer_hp_hc(energie, offre.plages_hc) if offre.plages_hc else energie.assign(periode="HP")
    kwh_hc = float(hc.loc[hc["periode"] == "HC", "kwh"].sum())
    kwh_hp = float(hc.loc[hc["periode"] == "HP", "kwh"].sum())
    nb_jours = (energie["fin"].max() - energie["debut"].min()).total_seconds() / 86400
    abonnement = offre.abonnement_mensuel * nb_jours / JOURS_PAR_MOIS
    energie = kwh_hp * offre.prix_hp + kwh_hc * offre.prix_hc
    return {
        "offre": offre.nom,
        "kwh_hp": kwh_hp,
        "kwh_hc": kwh_hc,
        "abonnement": abonnement,
        "energie": energie,
        "total": abonnement + energie,
        "total_annuel": (abonnement + energie) * 365 / nb_jours if nb_jours else 0.0,
    }


def comparer_offres_kwh(kwh_hp: float, kwh_hc: float, nb_jours: float, offres: list[Offre]) -> list[dict]:
    """Comparaison quand on n'a que des totaux HP/HC (relevés manuels).

    Les offres ayant d'autres plages que les vôtres ne peuvent pas être simulées
    sans courbe de charge : on applique la même répartition HP/HC.
    """
    res = []
    for o in offres:
        if o.plages_hc:
            energie = kwh_hp * o.prix_hp + kwh_hc * o.prix_hc
        else:
            energie = (kwh_hp + kwh_hc) * o.prix_hp
        abonnement = o.abonnement_mensuel * nb_jours / JOURS_PAR_MOIS
        total = abonnement + energie
        res.append({
            "offre": o.nom, "abonnement": abonnement, "energie": energie, "total": total,
            "total_annuel": total * 365 / nb_jours if nb_jours else 0.0,
        })
    return res


def part_hc_rentable(abonnement_hc: float, prix_hp: float, prix_hc: float,
                     abonnement_base: float, prix_base: float, kwh_par_an: float) -> float | None:
    """Part HC minimale pour que l'option HC soit moins chère que Base.

    Retourne None si l'option HC n'est jamais rentable avec ces prix.
    """
    if kwh_par_an <= 0 or prix_hp == prix_hc:
        return None
    surcout_abo = (abonnement_hc - abonnement_base) * 12
    # coût HC = abo_hc + E*(p_hp - x*(p_hp - p_hc)) ; coût base = abo_base + E*p_base
    x = (surcout_abo + kwh_par_an * (prix_hp - prix_base)) / (kwh_par_an * (prix_hp - prix_hc))
    if x > 1:
        return None
    return max(x, 0.0)
