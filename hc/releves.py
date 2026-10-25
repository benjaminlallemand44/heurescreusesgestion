"""Relevés manuels des index HP/HC du compteur Linky."""

from pathlib import Path

import pandas as pd

from hc.calculs import ajouter_cout
from hc.tarifs import HistoriqueTarifs

COLONNES = ["date", "index_hc", "index_hp"]


def charger(chemin: Path) -> pd.DataFrame:
    if not chemin.exists():
        return pd.DataFrame(columns=COLONNES).astype(
            {"date": "datetime64[ns]", "index_hc": float, "index_hp": float}
        )
    df = pd.read_csv(chemin, parse_dates=["date"])
    return df[COLONNES].sort_values("date").reset_index(drop=True)


def sauvegarder(releves: pd.DataFrame, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    releves.sort_values("date").to_csv(chemin, index=False)


def verifier_nouveau(releves: pd.DataFrame, date, index_hc: float, index_hp: float) -> list[str]:
    """Contrôle de cohérence d'un relevé avec ceux qui l'entourent."""
    erreurs = []
    date = pd.Timestamp(date)
    if (releves["date"] == date).any():
        erreurs.append("Un relevé existe déjà à cette date et heure.")
    avant = releves[releves["date"] < date].tail(1)
    apres = releves[releves["date"] > date].head(1)
    for nom, valeur in (("HC", index_hc), ("HP", index_hp)):
        col = f"index_{nom.lower()}"
        if not avant.empty and valeur < avant[col].iloc[0]:
            erreurs.append(f"L'index {nom} ({valeur:g}) est inférieur au relevé précédent ({avant[col].iloc[0]:g}).")
        if not apres.empty and valeur > apres[col].iloc[0]:
            erreurs.append(f"L'index {nom} ({valeur:g}) est supérieur au relevé suivant ({apres[col].iloc[0]:g}).")
    return erreurs


def ajouter(releves: pd.DataFrame, date, index_hc: float, index_hp: float) -> pd.DataFrame:
    nouveau = pd.DataFrame([{"date": pd.Timestamp(date), "index_hc": index_hc, "index_hp": index_hp}])
    return pd.concat([releves, nouveau], ignore_index=True).sort_values("date").reset_index(drop=True)


def periodes(releves: pd.DataFrame, historique: HistoriqueTarifs) -> pd.DataFrame:
    """Consommation et coût entre deux relevés consécutifs.

    Le prix appliqué est celui en vigueur à la fin de chaque période.
    """
    df = releves.sort_values("date").reset_index(drop=True)
    res = pd.DataFrame({
        "debut": df["date"].shift(),
        "fin": df["date"],
        "kwh_hc": df["index_hc"].diff(),
        "kwh_hp": df["index_hp"].diff(),
    }).dropna().reset_index(drop=True)
    res["jours"] = (res["fin"] - res["debut"]).dt.total_seconds() / 86400
    res["kwh_total"] = res["kwh_hc"] + res["kwh_hp"]
    res["part_hc"] = (res["kwh_hc"] / res["kwh_total"]).where(res["kwh_total"] > 0)
    res = ajouter_cout(res, historique, "fin")
    res["abonnement"] = res["abonnement_jour"] * res["jours"]
    return res.drop(columns="abonnement_jour")


def bilan_journalier(releves: pd.DataFrame, historique: HistoriqueTarifs) -> pd.DataFrame:
    """Répartit chaque période entre ses jours, pour les estimations de facture."""
    lignes = []
    for _, p in periodes(releves, historique).iterrows():
        if p["jours"] <= 0:
            continue
        for jour in pd.date_range(p["debut"].normalize(), p["fin"] - pd.Timedelta(seconds=1), freq="D"):
            # fraction de la journée couverte par la période
            debut = max(jour, p["debut"])
            fin = min(jour + pd.Timedelta(days=1), p["fin"])
            frac = (fin - debut).total_seconds() / 86400 / p["jours"]
            lignes.append({"jour": jour.date(), "kwh_hp": p["kwh_hp"] * frac, "kwh_hc": p["kwh_hc"] * frac,
                           "couverture": (fin - debut).total_seconds() / 86400})
    if not lignes:
        return pd.DataFrame(columns=["jour", "kwh_hp", "kwh_hc", "kwh_total", "cout_energie", "abonnement_jour"])
    df = pd.DataFrame(lignes).groupby("jour", as_index=False).sum()
    df["kwh_total"] = df["kwh_hp"] + df["kwh_hc"]
    df = ajouter_cout(df, historique, "jour")
    df["abonnement_jour"] *= df["couverture"]
    return df.drop(columns="couverture")
