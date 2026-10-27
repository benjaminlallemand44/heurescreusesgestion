"""Récupération de la courbe de charge via Conso API (https://conso.boris.sh).

Conso API redistribue les données Enedis après votre consentement. Le token
est un secret : il se met dans .streamlit/secrets.toml ou dans la variable
d'environnement CONSO_API_TOKEN, jamais dans le code.
"""

import base64
import json
import re
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


class ErreurConsoAPI(Exception):
    """Erreur renvoyée par Conso API, avec son message explicatif."""


def nettoyer_token(token: str) -> str:
    """Retire espaces, guillemets et un éventuel « Bearer » collés par erreur."""
    token = token.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def prms_du_token(token: str) -> list[str]:
    """PRM autorisés lus dans le token (un JWT dont le contenu liste les PRM).

    Lecture seule, sans vérification de signature : sert uniquement à éviter
    de demander le PRM. Retourne une liste vide si le token n'est pas lisible.
    """
    morceaux = nettoyer_token(token).split(".")
    if len(morceaux) != 3:
        return []
    try:
        contenu = morceaux[1] + "=" * (-len(morceaux[1]) % 4)
        donnees = json.loads(base64.urlsafe_b64decode(contenu))
    except (ValueError, UnicodeDecodeError):
        return []
    trouves = []

    def parcourir(valeur):
        if isinstance(valeur, dict):
            for v in valeur.values():
                parcourir(v)
        elif isinstance(valeur, list):
            for v in valeur:
                parcourir(v)
        elif isinstance(valeur, (str, int)) and re.fullmatch(r"\d{14}", str(valeur)):
            if str(valeur) not in trouves:
                trouves.append(str(valeur))

    # « sub » d'abord : c'est là que Conso API range les PRM
    parcourir(donnees.get("sub") if isinstance(donnees, dict) else None)
    parcourir(donnees)
    return trouves


def _message_erreur(rep) -> str:
    try:
        corps = rep.json()
    except ValueError:
        return rep.text[:300] or rep.reason
    if isinstance(corps, dict):
        return str(corps.get("message") or corps.get("error") or corps)
    return str(corps)


def recuperer_courbe(prm: str, token: str, debut: date, fin: date, session=None) -> tuple[pd.DataFrame, list[str]]:
    """Courbe de charge de `debut` (inclus) à `fin` (exclu).

    Chaque tranche de 7 jours est demandée séparément : une tranche refusée
    (souvent antérieure à l'activation de la collecte) n'empêche pas
    d'afficher les autres. Retourne la courbe et la liste des erreurs ;
    lève ErreurConsoAPI si aucune tranche n'a abouti.
    """
    session = session or requests.Session()
    entetes = {"Authorization": f"Bearer {nettoyer_token(token)}", "User-Agent": USER_AGENT}
    morceaux, erreurs = [], []
    for d, f in decouper_periode(debut, fin):
        rep = session.get(
            f"{URL_BASE}/consumption_load_curve",
            params={"prm": prm, "start": d.isoformat(), "end": f.isoformat()},
            headers=entetes,
            timeout=30,
        )
        if rep.ok:
            morceaux.append(parse_courbe(rep.json()))
        else:
            erreurs.append(f"du {d:%d/%m} au {f:%d/%m} : {rep.status_code} — {_message_erreur(rep)}")
    if erreurs and not morceaux:
        raise ErreurConsoAPI(" ; ".join(erreurs))
    if not morceaux:
        return pd.DataFrame(columns=["date", "puissance_w"]), erreurs
    courbe = pd.concat(morceaux).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return courbe, erreurs


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
