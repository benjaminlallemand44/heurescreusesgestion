"""Apprentissage de la consommation réelle de chaque appareil.

Principe, adapté à une courbe au pas de 30 min :
1. chaque jour, on estime la consommation de fond (puissance basse hors
   utilisations annotées) ;
2. pendant une utilisation annotée, l'excédent au-dessus de ce fond est
   attribué à l'appareil (partagé si plusieurs tournent en même temps) ;
3. on retient, par appareil, la consommation médiane par utilisation ;
4. les pics non annotés qui ressemblent à un appareil appris sont proposés
   à la confirmation : chaque confirmation affine l'apprentissage.
"""

import pandas as pd

from hc import appareils as app
from hc import journal as jr
from hc.tarifs import Plage

QUANTILE_FOND = 0.2
COUVERTURE_MIN = 0.8  # part de l'utilisation couverte par des données de courbe
TOLERANCE_SUGGESTION = 0.4  # écart relatif de kWh accepté pour proposer un appareil
TOLERANCE_PUISSANCE = 0.5  # écart relatif de puissance moyenne accepté


def _chevauchement(a0, a1, b0, b1) -> float:
    """Durée commune (secondes) entre [a0, a1[ et [b0, b1[."""
    return max((min(a1, b1) - max(a0, b0)).total_seconds(), 0.0)


def _puissance_declaree(a) -> float:
    if a["usage"] == "cycle" and a["kwh_cycle"] > 0 and a["duree_h"] > 0:
        return a["kwh_cycle"] / a["duree_h"] * 1000
    return float(a["puissance_w"]) or 1.0


def _preparer(energie: pd.DataFrame, journal: pd.DataFrame, appareils: pd.DataFrame, a_l_instant=None):
    inv = app.normaliser(appareils).set_index("nom")
    j = journal.copy()
    j["fin_eff"] = jr.fins_effectives(j, appareils, a_l_instant)
    e = energie.copy()
    e["jour"] = e["debut"].dt.date
    return inv, j, e


def fond_par_jour(e: pd.DataFrame, j: pd.DataFrame) -> dict:
    """Puissance de fond de chaque jour, hors intervalles où un appareil annoté tourne."""
    fonds = {}
    for jour, ej in e.groupby("jour"):
        libres = [
            not any(_chevauchement(r.debut, r.fin, u.debut, u.fin_eff) > 0 for u in j.itertuples())
            for r in ej.itertuples()
        ] if len(j) else [True] * len(ej)
        p_libres = ej.loc[libres, "puissance_w"]
        fonds[jour] = float((p_libres if len(p_libres) >= 4 else ej["puissance_w"]).quantile(QUANTILE_FOND))
    return fonds


def observations(energie: pd.DataFrame, journal: pd.DataFrame, appareils: pd.DataFrame,
                 a_l_instant=None) -> pd.DataFrame:
    """Consommation mesurée de chaque utilisation annotée couverte par la courbe."""
    colonnes = ["id", "appareil", "debut", "fin", "duree_h", "kwh_mesure", "puissance_mesuree_w"]
    if energie.empty or journal.empty:
        return pd.DataFrame(columns=colonnes)
    inv, j, e = _preparer(energie, journal, appareils, a_l_instant)
    j = j[(j["fin_eff"] > e["debut"].min()) & (j["debut"] < e["fin"].max())]
    if j.empty:
        return pd.DataFrame(columns=colonnes)
    fonds = fond_par_jour(e, j)
    poids_declare = {u.id: (_puissance_declaree(inv.loc[u.appareil]) if u.appareil in inv.index else 1.0)
                     for u in j.itertuples()}
    kwh = {u.id: 0.0 for u in j.itertuples()}
    couvert = {u.id: 0.0 for u in j.itertuples()}

    for r in e.itertuples():
        recouvrements = {u.id: _chevauchement(r.debut, r.fin, u.debut, u.fin_eff) for u in j.itertuples()}
        recouvrements = {k: v for k, v in recouvrements.items() if v > 0}
        if not recouvrements:
            continue
        exces_w = max(r.puissance_w - fonds[r.jour], 0.0)
        poids = {k: poids_declare[k] * v for k, v in recouvrements.items()}
        total_poids = sum(poids.values())
        for k, v in recouvrements.items():
            couvert[k] += v
            # excédent pendant le recouvrement, partagé entre appareils simultanés
            kwh[k] += exces_w * v / 3.6e6 * (poids[k] / total_poids if len(poids) > 1 else 1.0)

    lignes = []
    for u in j.itertuples():
        duree_s = (u.fin_eff - u.debut).total_seconds()
        if duree_s <= 0 or couvert[u.id] / duree_s < COUVERTURE_MIN:
            continue
        lignes.append({
            "id": u.id, "appareil": u.appareil, "debut": u.debut, "fin": u.fin_eff,
            "duree_h": duree_s / 3600, "kwh_mesure": kwh[u.id],
            "puissance_mesuree_w": kwh[u.id] * 3.6e6 / duree_s,
        })
    return pd.DataFrame(lignes, columns=colonnes)


def fiabilite(n: int) -> str:
    return "faible" if n < 3 else "moyenne" if n < 6 else "bonne"


def apprendre(obs: pd.DataFrame, appareils: pd.DataFrame) -> pd.DataFrame:
    """Valeurs apprises par appareil, comparées à vos réglages."""
    inv = app.bilan_inventaire(appareils).set_index("nom")
    colonnes = ["appareil", "n", "kwh_appris", "puissance_apprise_w", "duree_mediane_h",
                "kwh_declare", "fiabilite", "kwh_retenu"]
    if obs.empty:
        return pd.DataFrame(columns=colonnes)
    g = obs.groupby("appareil").agg(
        n=("id", "count"), kwh_appris=("kwh_mesure", "median"),
        puissance_apprise_w=("puissance_mesuree_w", "median"), duree_mediane_h=("duree_h", "median"),
    ).reset_index()
    g["kwh_declare"] = g["appareil"].map(inv["kwh_utilisation"])
    g["fiabilite"] = g["n"].map(fiabilite)
    # Une seule mesure peut être trompeuse : on garde la valeur déclarée tant qu'il y en a moins de 2
    g["kwh_retenu"] = g["kwh_appris"].where(g["n"] >= 2, g["kwh_declare"].fillna(g["kwh_appris"]))
    return g[colonnes]


def repartition(energie: pd.DataFrame, obs: pd.DataFrame, appareils: pd.DataFrame) -> pd.DataFrame:
    """Répartition estimée de la consommation d'une période entre les appareils.

    Appareils permanents : d'après leur puissance déclarée ; utilisations
    annotées : consommation mesurée ; le reste est « non identifié ».
    """
    total = float(energie["kwh"].sum())
    jours = (energie["fin"].max() - energie["debut"].min()).total_seconds() / 86400 if len(energie) else 0
    lignes = []
    for _, a in app.normaliser(appareils).iterrows():
        if a["usage"] == "permanent":
            lignes.append({"poste": a["nom"], "categorie": a["categorie"],
                           "kwh": a["puissance_w"] * a["quantite"] * 24 * jours / 1000})
    if not obs.empty:
        inv = app.normaliser(appareils).set_index("nom")["categorie"]
        for nom, kwh in obs.groupby("appareil")["kwh_mesure"].sum().items():
            lignes.append({"poste": nom, "categorie": inv.get(nom, "Autre"), "kwh": kwh})
    df = pd.DataFrame(lignes, columns=["poste", "categorie", "kwh"])
    df = df[df["kwh"] > 0]
    reste = total - df["kwh"].sum()
    if reste > 0:
        df = pd.concat([df, pd.DataFrame([{"poste": "Non identifié", "categorie": "Non identifié", "kwh": reste}])])
    df["part"] = df["kwh"] / total if total else 0.0
    return df.sort_values("kwh", ascending=False).reset_index(drop=True)


def pics_non_annotes(energie: pd.DataFrame, journal: pd.DataFrame, appareils: pd.DataFrame,
                     seuil_w: float = 400, a_l_instant=None) -> pd.DataFrame:
    """Périodes où la puissance dépasse nettement le fond sans utilisation annotée."""
    colonnes = ["debut", "fin", "kwh", "puissance_w"]
    if energie.empty:
        return pd.DataFrame(columns=colonnes)
    _, j, e = _preparer(energie, journal, appareils, a_l_instant)
    fonds = fond_par_jour(e, j)
    pics, courant = [], None
    for r in e.sort_values("debut").itertuples():
        exces = r.puissance_w - fonds[r.jour]
        annote = any(_chevauchement(r.debut, r.fin, u.debut, u.fin_eff) > 0 for u in j.itertuples())
        if exces >= seuil_w and not annote:
            kwh = exces * (r.fin - r.debut).total_seconds() / 3.6e6
            if courant and courant["fin"] == r.debut:
                courant["fin"] = r.fin
                courant["kwh"] += kwh
            else:
                courant = {"debut": r.debut, "fin": r.fin, "kwh": kwh}
                pics.append(courant)
        else:
            courant = None
    if not pics:
        return pd.DataFrame(columns=colonnes)
    df = pd.DataFrame(pics, columns=["debut", "fin", "kwh"])
    df["puissance_w"] = df["kwh"] * 3.6e6 / (df["fin"] - df["debut"]).dt.total_seconds()
    return df[colonnes]


def _au_debut_des_heures_creuses(debut: pd.Timestamp, plages: list[Plage]) -> bool:
    """Vrai si un pic démarre dans l'heure qui suit (ou précède) le début d'une plage creuse."""
    for p in plages:
        for jour in (debut.normalize() - pd.Timedelta(days=1), debut.normalize()):
            ouverture = jour + pd.Timedelta(hours=p.debut.hour, minutes=p.debut.minute)
            if abs((debut - ouverture).total_seconds()) < 3600:
                return True
    return False


def suggestions(energie: pd.DataFrame, journal: pd.DataFrame, appareils: pd.DataFrame,
                appris: pd.DataFrame, seuil_w: float = 400, a_l_instant=None,
                plages: list[Plage] | None = None) -> pd.DataFrame:
    """Pour chaque pic non annoté, l'appareil qui lui ressemble le plus.

    Un pic qui démarre au début d'une plage creuse est attribué au ballon
    d'eau chaude (commandé par le contact heures creuses du compteur). Sinon,
    on compare l'énergie et la puissance moyenne du pic à celles d'une
    utilisation de chaque appareil : valeurs apprises si elles existent,
    sinon valeurs de vos réglages.
    """
    pics = pics_non_annotes(energie, journal, appareils, seuil_w, a_l_instant)
    colonnes = ["debut", "fin", "kwh", "puissance_w", "appareil", "d_apres"]
    if pics.empty:
        return pd.DataFrame(columns=colonnes)
    references = {}
    inv = app.bilan_inventaire(appareils)
    for _, a in inv.iterrows():
        if a["usage"] != "permanent" and a["kwh_utilisation"] > 0:
            references[a["nom"]] = (a["kwh_utilisation"], _puissance_declaree(a), "vos réglages")
    for _, a in appris.iterrows():
        if a["n"] >= 2 and a["kwh_appris"] > 0:
            references[a["appareil"]] = (a["kwh_appris"], a["puissance_apprise_w"], "l'apprentissage")
    ballons = inv.loc[inv["categorie"] == "Eau chaude", "nom"].tolist()
    lignes = []
    for _, p in pics.iterrows():
        if plages and ballons and _au_debut_des_heures_creuses(p["debut"], plages):
            lignes.append({**p.to_dict(), "appareil": ballons[0], "d_apres": "le début des heures creuses"})
            continue
        meilleur, score_min, source = None, float("inf"), ""
        for nom, (kwh_ref, w_ref, d_apres) in references.items():
            ecart_kwh = abs(p["kwh"] - kwh_ref) / kwh_ref
            ecart_w = abs(p["puissance_w"] - w_ref) / w_ref if w_ref > 0 else 1.0
            # l'énergie doit coller ; la puissance départage (un ballon de 2 200 W n'est pas un radiateur de 1 000 W)
            if ecart_kwh <= TOLERANCE_SUGGESTION and ecart_w <= TOLERANCE_PUISSANCE \
                    and ecart_kwh + ecart_w < score_min:
                meilleur, score_min, source = nom, ecart_kwh + ecart_w, d_apres
        lignes.append({**p.to_dict(), "appareil": meilleur, "d_apres": source})
    return pd.DataFrame(lignes, columns=colonnes)
