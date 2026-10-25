"""Planification des appareils en heures creuses et rappels calendrier."""

from datetime import date, datetime, timedelta

import pandas as pd

from hc.tarifs import Plage

COLONNES = ["nom", "kwh_cycle", "duree_h", "cycles_semaine"]

# Valeurs typiques, proposées au premier lancement puis modifiables
CATALOGUE = pd.DataFrame([
    {"nom": "Lave-linge (40 °C)", "kwh_cycle": 0.9, "duree_h": 2.0, "cycles_semaine": 3},
    {"nom": "Lave-linge (60 °C)", "kwh_cycle": 1.4, "duree_h": 2.5, "cycles_semaine": 1},
    {"nom": "Lave-vaisselle (éco)", "kwh_cycle": 0.9, "duree_h": 3.5, "cycles_semaine": 4},
    {"nom": "Sèche-linge (pompe à chaleur)", "kwh_cycle": 1.5, "duree_h": 2.5, "cycles_semaine": 2},
    {"nom": "Sèche-linge (condensation)", "kwh_cycle": 3.5, "duree_h": 2.0, "cycles_semaine": 2},
    {"nom": "Voiture électrique (recharge)", "kwh_cycle": 15.0, "duree_h": 5.0, "cycles_semaine": 2},
    {"nom": "Vélo / trottinette électrique", "kwh_cycle": 0.5, "duree_h": 4.0, "cycles_semaine": 2},
], columns=COLONNES)


def meilleur_creneau(plages: list[Plage], duree_h: float) -> Plage | None:
    """Plage HC la plus longue, si le cycle y tient entièrement."""
    candidates = [p for p in plages if p.duree_heures() >= duree_h]
    return max(candidates, key=Plage.duree_heures) if candidates else None


def plan_appareils(appareils: pd.DataFrame, plages: list[Plage], prix_hp: float, prix_hc: float) -> pd.DataFrame:
    """Heure de lancement conseillée et gain si chaque cycle passe de HP à HC."""
    lignes = []
    for _, a in appareils.dropna(subset=COLONNES).iterrows():
        creneau = meilleur_creneau(plages, float(a["duree_h"]))
        gain_cycle = float(a["kwh_cycle"]) * (prix_hp - prix_hc)
        if creneau:
            conseil = f"Lancer à {creneau.debut:%H:%M} (fin avant {creneau.fin:%H:%M})"
        else:
            conseil = "Cycle plus long que vos plages HC : le lancer au début de la plus longue"
        lignes.append({
            "Appareil": a["nom"],
            "Conseil": conseil,
            "Gain par cycle (€)": gain_cycle,
            "Gain par an (€)": gain_cycle * float(a["cycles_semaine"]) * 52,
        })
    return pd.DataFrame(lignes)


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
