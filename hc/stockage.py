"""Sauvegarde locale des réglages et des données saisies (dossier data/)."""

import json
from pathlib import Path

import pandas as pd

from hc import appareils
from hc.tarifs import GRILLE_DEFAUT, HistoriqueTarifs

DOSSIER = Path(__file__).resolve().parent.parent / "data"
FICHIER_REGLAGES = DOSSIER / "reglages.json"
FICHIER_TARIFS = DOSSIER / "tarifs.csv"
FICHIER_APPAREILS = DOSSIER / "appareils.csv"
FICHIER_RELEVES = DOSSIER / "releves.csv"

REGLAGES_DEFAUT = {
    "plages_hc": "22:00-06:00",
    "prm": "",
    "puissance_souscrite_kva": 6,
    "seuil_pic_w": 1500,
}


def charger_reglages() -> dict:
    reglages = dict(REGLAGES_DEFAUT)
    if FICHIER_REGLAGES.exists():
        reglages.update(json.loads(FICHIER_REGLAGES.read_text(encoding="utf-8")))
    return reglages


def sauvegarder_reglages(reglages: dict) -> None:
    DOSSIER.mkdir(exist_ok=True)
    FICHIER_REGLAGES.write_text(json.dumps(reglages, indent=2, ensure_ascii=False), encoding="utf-8")


def charger_tarifs() -> HistoriqueTarifs:
    if FICHIER_TARIFS.exists():
        return HistoriqueTarifs.depuis_dataframe(pd.read_csv(FICHIER_TARIFS))
    return HistoriqueTarifs([GRILLE_DEFAUT])


def sauvegarder_tarifs(historique: HistoriqueTarifs) -> None:
    DOSSIER.mkdir(exist_ok=True)
    historique.vers_dataframe().to_csv(FICHIER_TARIFS, index=False)


def charger_appareils() -> pd.DataFrame | None:
    """Liste enregistrée, ou None si l'utilisateur ne l'a pas encore créée."""
    if FICHIER_APPAREILS.exists():
        return pd.read_csv(FICHIER_APPAREILS)[appareils.COLONNES]
    return None


def sauvegarder_appareils(df: pd.DataFrame) -> None:
    DOSSIER.mkdir(exist_ok=True)
    df.to_csv(FICHIER_APPAREILS, index=False)
