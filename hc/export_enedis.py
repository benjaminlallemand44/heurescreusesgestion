"""Lecture du fichier de courbe de charge téléchargé depuis l'espace client Enedis.

Le fichier (.xlsx, ou .csv) commence souvent par quelques lignes d'informations
(PRM, type de données, unité, pas…) avant le tableau des mesures « Horodate /
Valeur ». On cherche donc ce tableau plutôt que de supposer sa position.
"""

import csv
import io
import re
import unicodedata
from pathlib import Path

import pandas as pd

MOTS_DATE = ("horodate", "date et heure", "date", "debut", "heure")
MOTS_VALEUR = ("valeur", "puissance", "consommation", "mesure")
UNITES = ("w", "wh", "kw", "kwh", "va", "kva")


def _normaliser(texte) -> str:
    """Minuscules, sans accents ni espaces superflus."""
    texte = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    return " ".join(texte.lower().split())


def _feuilles(fichier, nom: str) -> list[pd.DataFrame]:
    """Contenu brut (sans en-tête) de chaque feuille du fichier."""
    suffixe = Path(nom).suffix.lower()
    if suffixe in (".xlsx", ".xlsm", ".xls"):
        return list(pd.read_excel(fichier, sheet_name=None, header=None, dtype=object).values())
    brut = fichier.read() if hasattr(fichier, "read") else Path(fichier).read_bytes()
    try:
        texte = brut.decode("utf-8-sig")
    except UnicodeDecodeError:  # export Windows
        texte = brut.decode("latin-1")
    separateur = ";" if texte.count(";") >= texte.count(",") else ","
    lignes = [l for l in csv.reader(io.StringIO(texte), delimiter=separateur) if any(c.strip() for c in l)]
    largeur = max((len(l) for l in lignes), default=0)
    return [pd.DataFrame([l + [None] * (largeur - len(l)) for l in lignes], dtype=object)]


def _ligne_entete(df: pd.DataFrame) -> tuple[int, int, int, int | None] | None:
    """(ligne, colonne date, colonne valeur, colonne « Fin » éventuelle) du tableau des mesures.

    « Horodate » d'abord : les lignes d'informations du haut du fichier
    contiennent aussi des « Date de début »."""
    for mots_date in (("horodate",), MOTS_DATE):
        for i, ligne in df.iterrows():
            cellules = {j: _normaliser(v) for j, v in ligne.items() if pd.notna(v) and str(v).strip()}
            col_date = next((j for mot in mots_date for j, v in cellules.items() if v.startswith(mot)), None)
            col_val = next((j for mot in MOTS_VALEUR for j, v in cellules.items()
                            if j != col_date and v.startswith(mot)), None)
            if col_date is not None and col_val is not None:
                col_fin = next((j for j, v in cellules.items()
                                if j not in (col_date, col_val) and (v == "fin" or v.startswith("date de fin"))), None)
                return i, col_date, col_val, col_fin
    return None


def _unite_entete(entete) -> str:
    """Unité écrite dans l'en-tête de la colonne, ex. « Valeur (en kW) »."""
    trouve = re.search(r"\(\s*(?:en\s+)?([a-z]+)\s*\)", _normaliser(entete))
    return trouve.group(1) if trouve and trouve.group(1) in UNITES else ""


def _unite(df: pd.DataFrame, jusqua: int) -> str:
    """Unité indiquée dans les lignes d'informations (case à droite ou en dessous de « Unité »)."""
    for i in range(jusqua):
        for j, v in df.iloc[i].items():
            if pd.notna(v) and _normaliser(v).startswith("unite"):
                voisins = [df.iat[i, j + 1] if j + 1 < df.shape[1] else None,
                           df.iat[i + 1, j] if i + 1 < jusqua else None]
                for voisin in voisins:
                    if pd.notna(voisin) and _normaliser(voisin) in UNITES:
                        return _normaliser(voisin)
    return ""


def _dates(valeurs: pd.Series) -> pd.Series:
    """Dates locales françaises sans fuseau, quel que soit le format du fichier."""
    if pd.api.types.is_datetime64_any_dtype(valeurs) or all(hasattr(v, "year") for v in valeurs):
        dates = pd.to_datetime(valeurs)
    else:
        textes = valeurs.astype(str).str.strip()
        if textes.str.contains(r"T|[+-]\d\d:\d\d$", regex=True).any():
            # ISO avec décalage (+01:00 / +02:00 selon l'heure d'été)
            return pd.to_datetime(textes, utc=True).dt.tz_convert("Europe/Paris").dt.tz_localize(None)
        iso = textes.str.match(r"^\d{4}-").all()
        dates = pd.to_datetime(textes, dayfirst=not iso)
    if dates.dt.tz is not None:
        dates = dates.dt.tz_convert("Europe/Paris").dt.tz_localize(None)
    return dates


def _nombres(valeurs: pd.Series) -> pd.Series:
    return pd.to_numeric(valeurs.astype(str).str.replace(",", ".").str.replace("\u202f", "").str.replace(" ", ""),
                         errors="coerce")


def lire_export(fichier, nom: str) -> pd.DataFrame:
    """Courbe de charge (colonnes `date` = fin d'intervalle, `puissance_w`).

    Lève ValueError avec une explication si le fichier ne convient pas.
    """
    for df in _feuilles(fichier, nom):
        entete = _ligne_entete(df)
        if entete is None:
            continue
        i, col_date, col_val, col_fin = entete
        # La courbe est repérée par la fin de chaque intervalle (convention Enedis) :
        # on prend la colonne « Fin » si elle existe, sinon « Début » + pas.
        col_reference = col_fin if col_fin is not None else col_date
        debut_seul = col_fin is None and _normaliser(df.iat[i, col_date]).startswith(("debut", "date de debut"))
        donnees = df.iloc[i + 1:, [col_reference, col_val]].dropna()
        donnees.columns = ["date", "valeur"]
        valeurs = _nombres(donnees["valeur"])
        donnees = donnees[valeurs.notna()]
        valeurs = valeurs[valeurs.notna()]
        if donnees.empty:
            continue
        courbe = pd.DataFrame({"date": _dates(donnees["date"]).values, "valeur": valeurs.values})
        # Passage à l'heure d'hiver : une heure apparaît deux fois avec la même étiquette.
        # On cumule les deux pour ne pas perdre d'énergie (la puissance de cette heure-là est doublée).
        courbe = courbe.groupby("date", as_index=False)["valeur"].sum()
        pas = courbe["date"].diff().median()
        if pd.isna(pas):
            raise ValueError("Le fichier ne contient qu'une seule mesure.")
        if pas >= pd.Timedelta(hours=23):
            raise ValueError(
                "Ce fichier contient une consommation par jour. Sur l'espace Enedis, téléchargez plutôt la "
                "consommation horaire (courbe de charge, par demi-heure).")
        if debut_seul:
            courbe["date"] = courbe["date"] + pas
        unite = _unite_entete(df.iat[i, col_val]) or _unite(df, i)
        heures = pas.total_seconds() / 3600
        if unite in ("kwh",):
            puissance = courbe["valeur"] * 1000 / heures
        elif unite == "wh":
            puissance = courbe["valeur"] / heures
        elif unite in ("kw", "kva"):
            puissance = courbe["valeur"] * 1000
        else:  # W, VA ou non précisé : puissance moyenne en watts (cas de la courbe de charge Enedis)
            puissance = courbe["valeur"]
        return pd.DataFrame({"date": courbe["date"], "puissance_w": puissance.astype(float)})
    raise ValueError(
        "Tableau des mesures introuvable : le fichier doit contenir une colonne de dates (« Horodate ») et une "
        "colonne de valeurs (« Valeur »). Vérifiez que c'est bien l'export de la consommation horaire.")
