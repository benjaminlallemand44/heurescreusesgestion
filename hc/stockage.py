"""Sauvegarde des réglages et des données saisies.

Deux implémentations avec les mêmes méthodes :
- StockageSupabase : base en ligne, pour l'application déployée ;
- StockageLocal : fichiers dans data/, pour un usage sur son ordinateur.
"""

import json
import os
from pathlib import Path

import pandas as pd
import requests

from hc import appareils, journal, releves
from hc.tarifs import COLONNES_GRILLE, GRILLE_DEFAUT, HistoriqueTarifs

DOSSIER = Path(__file__).resolve().parent.parent / "data"

REGLAGES_DEFAUT = {
    "plages_hc": "00:56-06:56; 14:56-16:56",
    "prm": "",
    "puissance_souscrite_kva": 6,
    "seuil_pic_w": 1500,
}


def nettoyer_appareils(df: pd.DataFrame) -> pd.DataFrame:
    """Retire les lignes vides du tableau et refuse les noms en double."""
    df = df.dropna(subset=["nom"])
    df = df[df["nom"].astype(str).str.strip() != ""].assign(nom=lambda d: d["nom"].astype(str).str.strip())
    doublons = df["nom"][df["nom"].duplicated()].unique()
    if len(doublons):
        raise ValueError(f"Appareil en double : {', '.join(doublons)}")
    return appareils.normaliser(df)


def _journal_vers_lignes(df: pd.DataFrame) -> list[dict]:
    """Utilisations au format JSON (dates ISO, fin vide → null)."""
    df = journal.normaliser(df)
    lignes = []
    for r in df.to_dict("records"):
        for col in ("debut", "fin"):
            r[col] = None if pd.isna(r[col]) else pd.Timestamp(r[col]).strftime("%Y-%m-%dT%H:%M:%S")
        lignes.append(r)
    return lignes


class StockageLocal:
    nom = "fichiers locaux (data/)"

    def __init__(self, dossier: Path = DOSSIER):
        self.dossier = Path(dossier)

    def _fichier(self, nom: str) -> Path:
        self.dossier.mkdir(parents=True, exist_ok=True)
        return self.dossier / nom

    def charger_reglages(self) -> dict:
        reglages = dict(REGLAGES_DEFAUT)
        f = self.dossier / "reglages.json"
        if f.exists():
            reglages.update(json.loads(f.read_text(encoding="utf-8")))
        return reglages

    def sauvegarder_reglages(self, reglages: dict) -> None:
        self._fichier("reglages.json").write_text(json.dumps(reglages, indent=2, ensure_ascii=False), encoding="utf-8")

    def charger_tarifs(self) -> HistoriqueTarifs:
        f = self.dossier / "tarifs.csv"
        if f.exists():
            return HistoriqueTarifs.depuis_dataframe(pd.read_csv(f))
        return HistoriqueTarifs([GRILLE_DEFAUT])

    def sauvegarder_tarifs(self, historique: HistoriqueTarifs) -> None:
        historique.vers_dataframe().to_csv(self._fichier("tarifs.csv"), index=False)

    def charger_appareils(self) -> pd.DataFrame | None:
        """Liste enregistrée, ou None si l'utilisateur ne l'a pas encore créée."""
        f = self.dossier / "appareils.csv"
        return appareils.normaliser(pd.read_csv(f)) if f.exists() else None

    def sauvegarder_appareils(self, df: pd.DataFrame) -> None:
        nettoyer_appareils(df).to_csv(self._fichier("appareils.csv"), index=False)

    def charger_releves(self) -> pd.DataFrame:
        f = self.dossier / "releves.csv"
        if not f.exists():
            return releves.vide()
        return releves.normaliser(pd.read_csv(f, parse_dates=["date"]))

    def sauvegarder_releves(self, df: pd.DataFrame) -> None:
        releves.normaliser(df).to_csv(self._fichier("releves.csv"), index=False)

    def charger_journal(self) -> pd.DataFrame:
        f = self.dossier / "utilisations.csv"
        if not f.exists():
            return journal.vide()
        return journal.normaliser(pd.read_csv(f, dtype={"id": str, "note": str}))

    def _ecrire_journal(self, df: pd.DataFrame) -> None:
        journal.normaliser(df).to_csv(self._fichier("utilisations.csv"), index=False)

    def ajouter_utilisation(self, utilisation: dict) -> None:
        df = self.charger_journal()
        self._ecrire_journal(pd.concat([df, pd.DataFrame([utilisation])], ignore_index=True))

    def modifier_utilisation(self, id_: str, **champs) -> None:
        df = self.charger_journal()
        for k, v in champs.items():
            df.loc[df["id"] == id_, k] = v
        self._ecrire_journal(df)

    def supprimer_utilisation(self, id_: str) -> None:
        df = self.charger_journal()
        self._ecrire_journal(df[df["id"] != id_])


def _liste_in(valeurs) -> str:
    """Liste PostgREST pour un filtre in.(…), valeurs entre guillemets."""
    echappees = (str(v).replace("\\", "\\\\").replace('"', '\\"') for v in valeurs)
    return "(" + ",".join(f'"{v}"' for v in echappees) + ")"


class StockageSupabase:
    """Accès aux tables via l'API REST de Supabase, avec la clé secrète du projet.

    La clé secrète contourne les règles d'accès : elle ne doit exister que côté
    serveur (secrets Streamlit), jamais dans le code ni dans le navigateur.
    """

    nom = "Supabase"

    def __init__(self, url: str, cle: str, session=None):
        self.url = url.rstrip("/") + "/rest/v1"
        self.session = session or requests.Session()
        self.entetes = {"apikey": cle, "Content-Type": "application/json"}
        if cle.startswith("eyJ"):  # ancienne clé service_role (JWT)
            self.entetes["Authorization"] = f"Bearer {cle}"

    def _requete(self, methode: str, table: str, params=None, json_=None, prefer=None):
        entetes = dict(self.entetes)
        if prefer:
            entetes["Prefer"] = prefer
        rep = self.session.request(methode, f"{self.url}/{table}", params=params, json=json_,
                                   headers=entetes, timeout=20)
        rep.raise_for_status()
        return rep.json() if rep.content else None

    def _lire(self, table: str, ordre: str) -> list[dict]:
        return self._requete("GET", table, params={"select": "*", "order": ordre})

    def _remplacer(self, table: str, cle: str, lignes: list[dict]) -> None:
        """Enregistre `lignes` comme contenu complet de la table.

        D'abord l'upsert (ajouts et modifications), ensuite seulement la
        suppression des lignes retirées : une erreur réseau entre les deux
        ne fait rien perdre.
        """
        if lignes:
            self._requete("POST", table, params={"on_conflict": cle}, json_=lignes,
                          prefer="resolution=merge-duplicates,return=minimal")
            filtre = f"not.in.{_liste_in(l[cle] for l in lignes)}"
        else:
            filtre = "not.is.null"
        self._requete("DELETE", table, params={cle: filtre}, prefer="return=minimal")

    def charger_reglages(self) -> dict:
        reglages = dict(REGLAGES_DEFAUT)
        lignes = self._lire("reglages", "id")
        if lignes:
            reglages.update(lignes[0]["valeurs"])
        return reglages

    def sauvegarder_reglages(self, reglages: dict) -> None:
        self._requete("POST", "reglages", params={"on_conflict": "id"}, json_=[{"id": 1, "valeurs": reglages}],
                      prefer="resolution=merge-duplicates,return=minimal")

    def charger_tarifs(self) -> HistoriqueTarifs:
        lignes = self._lire("tarifs", "date_debut")
        if not lignes:
            return HistoriqueTarifs([GRILLE_DEFAUT])
        return HistoriqueTarifs.depuis_dataframe(pd.DataFrame(lignes)[COLONNES_GRILLE])

    def sauvegarder_tarifs(self, historique: HistoriqueTarifs) -> None:
        df = historique.vers_dataframe()
        df["date_debut"] = pd.to_datetime(df["date_debut"]).dt.strftime("%Y-%m-%d")
        self._remplacer("tarifs", "date_debut", df.to_dict("records"))

    def charger_appareils(self) -> pd.DataFrame | None:
        lignes = self._lire("appareils", "nom")
        return appareils.normaliser(pd.DataFrame(lignes)) if lignes else None

    def sauvegarder_appareils(self, df: pd.DataFrame) -> None:
        self._remplacer("appareils", "nom", nettoyer_appareils(df).to_dict("records"))

    def charger_releves(self) -> pd.DataFrame:
        lignes = self._lire("releves", "date")
        if not lignes:
            return releves.vide()
        df = pd.DataFrame(lignes)
        df["date"] = pd.to_datetime(df["date"])
        return releves.normaliser(df)

    def sauvegarder_releves(self, df: pd.DataFrame) -> None:
        df = releves.normaliser(df)
        df["date"] = df["date"].dt.strftime("%Y-%m-%dT%H:%M:%S")
        self._remplacer("releves", "date", df.to_dict("records"))

    def charger_journal(self) -> pd.DataFrame:
        lignes = self._lire("utilisations", "debut")
        return journal.normaliser(pd.DataFrame(lignes)) if lignes else journal.vide()

    def ajouter_utilisation(self, utilisation: dict) -> None:
        self._requete("POST", "utilisations", json_=_journal_vers_lignes(pd.DataFrame([utilisation])),
                      prefer="return=minimal")

    def modifier_utilisation(self, id_: str, **champs) -> None:
        valeurs = {k: (None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%S"))
                   if k in ("debut", "fin") else v for k, v in champs.items()}
        self._requete("PATCH", "utilisations", params={"id": f"eq.{id_}"}, json_=valeurs, prefer="return=minimal")

    def supprimer_utilisation(self, id_: str) -> None:
        self._requete("DELETE", "utilisations", params={"id": f"eq.{id_}"}, prefer="return=minimal")


def choisir(secrets: dict | None = None):
    """Supabase si SUPABASE_URL et SUPABASE_SECRET_KEY sont configurés
    (secrets Streamlit ou variables d'environnement), sinon fichiers locaux."""
    secrets = secrets or {}
    url = secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    cle = secrets.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SECRET_KEY")
    if url and cle:
        return StockageSupabase(url, cle)
    return StockageLocal()
