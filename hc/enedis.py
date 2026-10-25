"""Récupération de la courbe de charge via Conso API (https://conso.boris.sh).

Conso API redistribue les données Enedis après votre consentement. Le token
est un secret : il se met dans .streamlit/secrets.toml ou dans la variable
d'environnement CONSO_API_TOKEN, jamais dans le code.
"""

from datetime import date, timedelta

import pandas as pd
import requests

URL_BASE = "https://conso.boris.sh/api"
USER_AGENT = "heurescreusesgestion (https://github.com/benjaminlallemand44/heurescreusesgestion)"
# Conso API limite la courbe de charge à 7 jours par requête
JOURS_MAX_PAR_REQUETE = 7


def parse_courbe(reponse: dict) -> pd.DataFrame:
    """Convertit la réponse JSON de consumption_load_curve en DataFrame."""
    lectures = reponse.get("interval_reading", [])
    df = pd.DataFrame(lectures, columns=["date", "value"])
    return pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "puissance_w": pd.to_numeric(df["value"]),
    })


def decouper_periode(debut: date, fin: date) -> list[tuple[date, date]]:
    """Découpe [debut, fin[ en tranches acceptées par l'API."""
    tranches = []
    courant = debut
    while courant < fin:
        suivant = min(courant + timedelta(days=JOURS_MAX_PAR_REQUETE), fin)
        tranches.append((courant, suivant))
        courant = suivant
    return tranches


def recuperer_courbe(prm: str, token: str, debut: date, fin: date, session=None) -> pd.DataFrame:
    """Courbe de charge de `debut` (inclus) à `fin` (exclu)."""
    session = session or requests.Session()
    entetes = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    morceaux = []
    for d, f in decouper_periode(debut, fin):
        rep = session.get(
            f"{URL_BASE}/consumption_load_curve",
            params={"prm": prm, "start": d.isoformat(), "end": f.isoformat()},
            headers=entetes,
            timeout=30,
        )
        rep.raise_for_status()
        morceaux.append(parse_courbe(rep.json()))
    if not morceaux:
        return pd.DataFrame(columns=["date", "puissance_w"])
    return pd.concat(morceaux).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def lire_csv(fichier) -> pd.DataFrame:
    """Lit un CSV `date;puissance_w` (ou `date,valeur`) exporté ou préparé à la main."""
    df = pd.read_csv(fichier, sep=None, engine="python")
    if len(df.columns) < 2:
        raise ValueError("Le fichier doit contenir au moins deux colonnes : date et puissance (W).")
    df = df.iloc[:, :2]
    df.columns = ["date", "puissance_w"]
    return pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "puissance_w": pd.to_numeric(df["puissance_w"]),
    })
