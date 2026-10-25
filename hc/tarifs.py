"""Plages d'heures creuses, grilles tarifaires datées et offres à comparer."""

from dataclasses import dataclass, field
from datetime import date, time

import pandas as pd


@dataclass(frozen=True)
class Plage:
    """Plage horaire, éventuellement à cheval sur minuit (ex. 22:00-06:00)."""

    debut: time
    fin: time

    def contient(self, t: time) -> bool:
        """Vrai si l'heure t est dans la plage (début inclus, fin exclue)."""
        if self.debut <= self.fin:
            return self.debut <= t < self.fin
        return t >= self.debut or t < self.fin

    def duree_heures(self) -> float:
        debut = self.debut.hour * 60 + self.debut.minute
        fin = self.fin.hour * 60 + self.fin.minute
        return ((fin - debut) % (24 * 60)) / 60

    def __str__(self) -> str:
        return f"{self.debut:%H:%M}-{self.fin:%H:%M}"


def parse_plages(texte: str) -> list[Plage]:
    """Lit des plages au format "22:00-06:00" ou "01:30-07:30; 12:30-14:30"."""
    plages = []
    for morceau in texte.replace(",", ";").split(";"):
        morceau = morceau.strip()
        if not morceau:
            continue
        try:
            debut_txt, fin_txt = (p.strip() for p in morceau.split("-"))
            plage = Plage(time.fromisoformat(debut_txt), time.fromisoformat(fin_txt))
        except ValueError as exc:
            raise ValueError(f"Plage invalide : « {morceau} » (format attendu HH:MM-HH:MM)") from exc
        if plage.debut == plage.fin:
            raise ValueError(f"Plage vide : « {morceau} »")
        plages.append(plage)
    if not plages:
        raise ValueError("Aucune plage d'heures creuses renseignée")
    return plages


def format_plages(plages: list[Plage]) -> str:
    return "; ".join(str(p) for p in plages)


def est_heure_creuse(t: time, plages: list[Plage]) -> bool:
    return any(p.contient(t) for p in plages)


@dataclass
class Grille:
    """Prix TTC (taxes comprises) en vigueur à partir de `date_debut`."""

    date_debut: date
    abonnement_hc_mensuel: float
    prix_hp: float
    prix_hc: float
    abonnement_base_mensuel: float
    prix_base: float


COLONNES_GRILLE = [
    "date_debut", "abonnement_hc_mensuel", "prix_hp", "prix_hc",
    "abonnement_base_mensuel", "prix_base",
]

# Valeurs indicatives (tarif bleu 6 kVA, février 2025) : à vérifier sur votre
# facture, les tarifs réglementés changent plusieurs fois par an.
GRILLE_DEFAUT = Grille(date(2025, 2, 1), 15.74, 0.2700, 0.2068, 15.47, 0.2516)


class HistoriqueTarifs:
    """Suite de grilles datées : chaque grille s'applique jusqu'à la suivante."""

    def __init__(self, grilles: list[Grille]):
        if not grilles:
            raise ValueError("Il faut au moins une grille tarifaire")
        self.grilles = sorted(grilles, key=lambda g: g.date_debut)

    def grille_a(self, jour: date) -> Grille:
        applicable = self.grilles[0]  # avant la 1re date, on prend la plus ancienne
        for g in self.grilles:
            if g.date_debut <= jour:
                applicable = g
        return applicable

    def actuelle(self) -> Grille:
        return self.grilles[-1]

    def vers_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([vars(g) for g in self.grilles], columns=COLONNES_GRILLE)

    @classmethod
    def depuis_dataframe(cls, df: pd.DataFrame) -> "HistoriqueTarifs":
        df = df.dropna(subset=COLONNES_GRILLE)
        grilles = [
            Grille(
                date_debut=pd.Timestamp(r["date_debut"]).date(),
                **{c: float(r[c]) for c in COLONNES_GRILLE[1:]},
            )
            for _, r in df.iterrows()
        ]
        return cls(grilles)


@dataclass
class Offre:
    """Offre à simuler. Sans plages, tout est facturé au prix HP (option Base)."""

    nom: str
    abonnement_mensuel: float
    prix_hp: float
    prix_hc: float = 0.0
    plages_hc: list[Plage] = field(default_factory=list)


def offres_depuis_grille(grille: Grille, plages: list[Plage]) -> list[Offre]:
    return [
        Offre("Heures creuses (actuelle)", grille.abonnement_hc_mensuel, grille.prix_hp, grille.prix_hc, plages),
        Offre("Base", grille.abonnement_base_mensuel, grille.prix_base),
    ]
