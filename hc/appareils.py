"""Inventaire des appareils, estimations de consommation et rappels calendrier."""

from datetime import date, datetime, timedelta

import pandas as pd

from hc.tarifs import Plage

COLONNES = [
    "nom", "categorie", "usage", "puissance_w", "duree_h", "kwh_cycle",
    "utilisations_semaine", "mois_par_an", "quantite", "deplacable",
]

CATEGORIES = [
    "Chauffage", "Eau chaude", "Cuisson", "Froid", "Lavage",
    "Éclairage", "Multimédia", "Petit électroménager", "Autre",
]

# Comment l'appareil consomme :
# - cycle : un programme à consommation connue (lave-linge) → kwh_cycle
# - duree : puissance × durée d'utilisation (plaques, radiateur, éclairage)
# - permanent : branché en continu, puissance moyenne (réfrigérateur, box)
USAGES = {"cycle": "Cycle / programme", "duree": "Durée d'utilisation", "permanent": "Permanent"}

DEFAUTS = {"categorie": "Autre", "usage": "duree", "puissance_w": 0.0, "duree_h": 1.0, "kwh_cycle": 0.0,
           "utilisations_semaine": 0.0, "mois_par_an": 12.0, "quantite": 1, "deplacable": False}


def _a(nom, categorie, usage, puissance_w=0.0, duree_h=0.0, kwh_cycle=0.0, utilisations_semaine=0.0,
       mois_par_an=12.0, deplacable=False):
    return {"nom": nom, "categorie": categorie, "usage": usage, "puissance_w": puissance_w, "duree_h": duree_h,
            "kwh_cycle": kwh_cycle, "utilisations_semaine": utilisations_semaine, "mois_par_an": mois_par_an,
            "quantite": 1, "deplacable": deplacable}


# Valeurs typiques, proposées au premier lancement puis modifiables.
# Chauffage : durée = équivalent pleine puissance par jour (le thermostat coupe souvent).
CATALOGUE = pd.DataFrame([
    _a("Radiateur séjour", "Chauffage", "duree", 1500, 5, utilisations_semaine=7, mois_par_an=6),
    _a("Radiateur chambre", "Chauffage", "duree", 1000, 4, utilisations_semaine=7, mois_par_an=6),
    _a("Sèche-serviettes", "Chauffage", "duree", 750, 1.5, utilisations_semaine=7, mois_par_an=6, deplacable=True),
    _a("Ballon d'eau chaude", "Eau chaude", "duree", 2200, 2.5, utilisations_semaine=7, deplacable=True),
    _a("Plaques de cuisson", "Cuisson", "duree", 2000, 0.75, utilisations_semaine=7),
    _a("Four", "Cuisson", "duree", 2000, 0.75, utilisations_semaine=2),
    _a("Micro-ondes", "Cuisson", "duree", 1000, 0.15, utilisations_semaine=7),
    _a("Bouilloire", "Cuisson", "duree", 2000, 0.05, utilisations_semaine=7),
    _a("Réfrigérateur-congélateur", "Froid", "permanent", 35),
    _a("Lave-linge", "Lavage", "cycle", 450, 2.0, 0.9, 3, deplacable=True),
    _a("Lave-vaisselle", "Lavage", "cycle", 260, 3.5, 0.9, 4, deplacable=True),
    _a("Sèche-linge", "Lavage", "cycle", 600, 2.5, 1.5, 2, deplacable=True),
    _a("Éclairage", "Éclairage", "duree", 80, 5, utilisations_semaine=7),
    _a("Box internet", "Multimédia", "permanent", 12),
    _a("Télévision", "Multimédia", "duree", 80, 3, utilisations_semaine=7),
    _a("Ordinateur", "Multimédia", "duree", 60, 4, utilisations_semaine=5),
    _a("Veilles diverses", "Multimédia", "permanent", 15),
    _a("Cafetière", "Petit électroménager", "duree", 1000, 0.1, utilisations_semaine=7),
    _a("Aspirateur", "Petit électroménager", "duree", 800, 0.5, utilisations_semaine=1),
    _a("Fer à repasser", "Petit électroménager", "duree", 2000, 0.5, utilisations_semaine=1),
], columns=COLONNES)


def normaliser(df: pd.DataFrame) -> pd.DataFrame:
    """Complète les colonnes manquantes (anciennes listes) avec les valeurs par défaut."""
    df = df.copy()
    if "cycles_semaine" in df and "utilisations_semaine" not in df:
        df = df.rename(columns={"cycles_semaine": "utilisations_semaine"})
        df["usage"] = df.get("usage", "cycle")
        df["deplacable"] = df.get("deplacable", True)
        df["categorie"] = df.get("categorie", "Lavage")
    for col in COLONNES:
        if col not in df:
            df[col] = DEFAUTS.get(col)
        elif col in DEFAUTS:
            df[col] = df[col].where(df[col].notna(), DEFAUTS[col])
    df["deplacable"] = df["deplacable"].astype(bool)
    df["quantite"] = df["quantite"].astype(int)
    for col in ("puissance_w", "duree_h", "kwh_cycle", "utilisations_semaine", "mois_par_an"):
        df[col] = df[col].astype(float)
    return df[COLONNES].reset_index(drop=True)


def kwh_par_utilisation(a) -> float:
    """kWh d'une utilisation (un cycle, une séance) ; pour un appareil permanent, par jour."""
    if a["usage"] == "permanent":
        return a["puissance_w"] * 24 / 1000
    if a["usage"] == "cycle" and a["kwh_cycle"] > 0:
        return float(a["kwh_cycle"])
    return a["puissance_w"] * a["duree_h"] / 1000


def kwh_annuel(a) -> float:
    saison = a["mois_par_an"] / 12
    if a["usage"] == "permanent":
        return a["puissance_w"] * 24 * 365 / 1000 * saison * a["quantite"]
    return kwh_par_utilisation(a) * a["utilisations_semaine"] * 52 * saison * a["quantite"]


def bilan_inventaire(appareils: pd.DataFrame) -> pd.DataFrame:
    """Consommation annuelle théorique de chaque appareil, d'après vos réglages."""
    df = normaliser(appareils)
    df["kwh_utilisation"] = [kwh_par_utilisation(a) for _, a in df.iterrows()]
    df["kwh_an"] = [kwh_annuel(a) for _, a in df.iterrows()]
    return df


def duree_typique(a) -> float:
    """Durée d'une utilisation en heures (1 h si non renseignée)."""
    if a["duree_h"] > 0:
        return float(a["duree_h"])
    return 1.0


def meilleur_creneau(plages: list[Plage], duree_h: float) -> Plage | None:
    """Plage HC la plus longue, si le cycle y tient entièrement."""
    candidates = [p for p in plages if p.duree_heures() >= duree_h]
    return max(candidates, key=Plage.duree_heures) if candidates else None


def plan_appareils(appareils: pd.DataFrame, plages: list[Plage], prix_hp: float, prix_hc: float) -> pd.DataFrame:
    """Heure de lancement conseillée et gain si chaque utilisation passe de HP à HC
    (appareils déplaçables uniquement)."""
    lignes = []
    for _, a in normaliser(appareils).iterrows():
        if not a["deplacable"] or a["usage"] == "permanent" or a["utilisations_semaine"] <= 0:
            continue
        creneau = meilleur_creneau(plages, duree_typique(a))
        gain = kwh_par_utilisation(a) * (prix_hp - prix_hc)
        if creneau:
            conseil = f"Lancer à {creneau.debut:%H:%M} (fin avant {creneau.fin:%H:%M})"
        else:
            conseil = "Plus long que vos plages HC : le lancer au début de la plus longue"
        lignes.append({
            "Appareil": a["nom"],
            "Conseil": conseil,
            "Gain par utilisation (€)": gain,
            "Gain par an (€)": gain * a["utilisations_semaine"] * 52 * a["mois_par_an"] / 12 * a["quantite"],
        })
    return pd.DataFrame(lignes, columns=["Appareil", "Conseil", "Gain par utilisation (€)", "Gain par an (€)"])


def _echapper_ics(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def calendrier_rappels(plages: list[Plage], minutes_avant: int = 10, debut: date | None = None) -> str:
    """Fichier .ics : un événement quotidien au début de chaque plage HC,
    avec une alarme `minutes_avant` minutes avant. À importer dans votre
    agenda (Google Agenda, Apple Calendrier, Outlook) pour les notifications."""
    debut = debut or date.today() + timedelta(days=1)
    horodatage = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//heurescreusesgestion//rappels HC//FR",
        "CALSCALE:GREGORIAN",
    ]
    for i, p in enumerate(plages):
        dt = datetime.combine(debut, p.debut)
        titre = _echapper_ics(f"Heures creuses ({p})")
        lignes += [
            "BEGIN:VEVENT",
            f"UID:hc-{i}-{p.debut:%H%M}@heurescreusesgestion",
            f"DTSTAMP:{horodatage}",
            f"DTSTART;TZID=Europe/Paris:{dt:%Y%m%dT%H%M%S}",
            "DURATION:PT15M",
            "RRULE:FREQ=DAILY",
            f"SUMMARY:{titre}",
            "DESCRIPTION:" + _echapper_ics("C'est le moment de lancer lave-linge, lave-vaisselle..."),
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{titre}",
            f"TRIGGER:-PT{minutes_avant}M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lignes.append("END:VCALENDAR")
    return "\r\n".join(lignes) + "\r\n"
