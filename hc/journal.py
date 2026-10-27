"""Journal d'utilisation des appareils : qui a fonctionné, de quelle heure à quelle heure.

Alimenté par le bouton « Je lance… » (sur le moment) ou par l'annotation de
la courbe (après coup). Sert à l'apprentissage et aux conseils.
"""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from hc import appareils as app

COLONNES = ["id", "appareil", "debut", "fin", "source", "note"]
SOURCES = {"bouton": "Bouton « Je lance »", "courbe": "Annotation de la courbe", "suggestion": "Suggestion confirmée"}
FUSEAU = ZoneInfo("Europe/Paris")
# Au-delà, une utilisation « en cours » oubliée est considérée comme terminée
DUREE_MAX_EN_COURS = timedelta(hours=12)


def maintenant() -> datetime:
    """Heure locale française, sans fuseau (comme les données Enedis).

    Le serveur Streamlit Cloud est en UTC : datetime.now() y serait décalé.
    """
    return datetime.now(FUSEAU).replace(tzinfo=None, second=0, microsecond=0)


def vide() -> pd.DataFrame:
    return pd.DataFrame({
        "id": pd.Series(dtype=str), "appareil": pd.Series(dtype=str),
        "debut": pd.Series(dtype="datetime64[ns]"), "fin": pd.Series(dtype="datetime64[ns]"),
        "source": pd.Series(dtype=str), "note": pd.Series(dtype=str),
    })


def normaliser(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=COLONNES).copy()
    for col in ("debut", "fin"):
        df[col] = pd.to_datetime(df[col]).dt.tz_localize(None).astype("datetime64[ns]")
    df["note"] = df["note"].fillna("").astype(str)
    df["source"] = df["source"].fillna("bouton").astype(str)
    df["id"] = df["id"].astype(str)
    return df.sort_values("debut").reset_index(drop=True)


def nouvelle(appareil: str, debut: datetime, fin: datetime | None = None,
             source: str = "bouton", note: str = "") -> dict:
    if fin is not None and fin <= debut:
        raise ValueError("L'heure de fin doit être après l'heure de début.")
    return {"id": uuid.uuid4().hex, "appareil": appareil, "debut": pd.Timestamp(debut),
            "fin": pd.Timestamp(fin) if fin is not None else pd.NaT, "source": source, "note": note}


def lancer(appareil_ligne, quand: datetime | None = None) -> dict:
    """Utilisation démarrée maintenant. Un cycle a une fin connue d'avance ;
    une utilisation « à la durée » reste en cours jusqu'à ce qu'on l'arrête."""
    debut = quand or maintenant()
    fin = None
    if appareil_ligne["usage"] == "cycle":
        fin = debut + timedelta(hours=app.duree_typique(appareil_ligne))
    return nouvelle(appareil_ligne["nom"], debut, fin, source="bouton")


def fins_effectives(journal: pd.DataFrame, appareils: pd.DataFrame, a_l_instant: datetime | None = None) -> pd.Series:
    """Fin de chaque utilisation : celle saisie, sinon maintenant si elle est
    en cours, sinon début + durée typique de l'appareil."""
    a_l_instant = pd.Timestamp(a_l_instant or maintenant())
    durees = {a["nom"]: app.duree_typique(a) for _, a in app.normaliser(appareils).iterrows()}
    fins = []
    for _, u in journal.iterrows():
        if pd.notna(u["fin"]):
            fins.append(u["fin"])
        elif a_l_instant - u["debut"] < DUREE_MAX_EN_COURS:
            fins.append(max(a_l_instant, u["debut"] + pd.Timedelta(minutes=1)))
        else:
            fins.append(u["debut"] + pd.Timedelta(hours=durees.get(u["appareil"], 1.0)))
    return pd.Series(fins, index=journal.index, dtype="datetime64[ns]")


def en_cours(journal: pd.DataFrame, a_l_instant: datetime | None = None) -> pd.DataFrame:
    a_l_instant = pd.Timestamp(a_l_instant or maintenant())
    masque = journal["fin"].isna() & (a_l_instant - journal["debut"] < DUREE_MAX_EN_COURS)
    return journal[masque]


def du_jour(journal: pd.DataFrame, jour) -> pd.DataFrame:
    debut = pd.Timestamp(jour)
    return journal[(journal["debut"] >= debut) & (journal["debut"] < debut + pd.Timedelta(days=1))]
