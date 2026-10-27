import json
from datetime import date, datetime

import pandas as pd
import pytest

from hc import appareils, releves, stockage
from hc import journal as jr
from hc.tarifs import GRILLE_DEFAUT, Grille, HistoriqueTarifs


def exemple_releves():
    df = releves.ajouter(releves.vide(), "2026-10-20 20:00", 12450, 8320)
    return releves.ajouter(df, "2026-10-21 20:00", 12458, 8323)


def test_local_aller_retour(tmp_path):
    s = stockage.StockageLocal(tmp_path)
    assert s.charger_releves().empty
    assert s.charger_appareils() is None
    assert s.charger_reglages()["plages_hc"] == "00:56-06:56; 14:56-16:56"

    s.sauvegarder_releves(exemple_releves())
    pd.testing.assert_frame_equal(s.charger_releves(), exemple_releves())

    s.sauvegarder_appareils(appareils.CATALOGUE.head(2))
    assert list(s.charger_appareils()["nom"]) == list(appareils.CATALOGUE["nom"].head(2))
    verifier_journal(s)

    h = HistoriqueTarifs([GRILLE_DEFAUT, Grille(date(2026, 2, 1), 16, 0.28, 0.21, 15.5, 0.26)])
    s.sauvegarder_tarifs(h)
    assert s.charger_tarifs().grilles == h.grilles

    s.sauvegarder_reglages({**stockage.REGLAGES_DEFAUT, "prm": "12345678901234"})
    assert s.charger_reglages()["prm"] == "12345678901234"


def test_appareils_lignes_vides_et_doublons():
    df = pd.concat([appareils.CATALOGUE.head(1), pd.DataFrame([{"nom": None}])], ignore_index=True)
    assert len(stockage.nettoyer_appareils(df)) == 1
    with pytest.raises(ValueError, match="double"):
        stockage.nettoyer_appareils(pd.concat([appareils.CATALOGUE.head(1)] * 2))


class FausseAPI:
    """Imite PostgREST : tables en mémoire, upsert et filtres utilisés par l'application."""

    def __init__(self):
        self.tables = {}
        self.requetes = []

    def request(self, methode, url, params, json, headers, timeout):
        table = url.rsplit("/", 1)[1]
        self.requetes.append((methode, table, params, headers))
        lignes = self.tables.setdefault(table, [])

        class Rep:
            content = b""

            def raise_for_status(self):
                pass

        rep = Rep()
        if methode == "GET":
            rep.content = b"x"
            rep.json = lambda: list(lignes)
        elif methode == "POST" and "on_conflict" in (params or {}):
            cle = params["on_conflict"]
            for nouvelle in json:
                lignes[:] = [l for l in lignes if l[cle] != nouvelle[cle]] + [nouvelle]
        elif methode == "POST" and "on_conflict" not in (params or {}):
            lignes.extend(json)
        elif methode == "PATCH":
            id_ = params["id"].removeprefix("eq.")
            for l in lignes:
                if l["id"] == id_:
                    l.update(json)
        elif methode == "DELETE":
            (cle, filtre), = params.items()
            if filtre.startswith("eq."):
                lignes[:] = [l for l in lignes if str(l[cle]) != filtre[3:]]
            elif filtre == "not.is.null":
                lignes.clear()
            else:
                gardees = {v.strip('"') for v in filtre.removeprefix("not.in.(").removesuffix(")").split('","')}
                lignes[:] = [l for l in lignes if str(l[cle]) in gardees]
        return rep


def test_supabase_aller_retour():
    api = FausseAPI()
    s = stockage.StockageSupabase("https://x.supabase.co", "sb_secret_abc", session=api)
    assert s.charger_releves().empty

    s.sauvegarder_releves(exemple_releves())
    assert api.tables["releves"][0]["date"] == "2026-10-20T20:00:00"
    pd.testing.assert_frame_equal(s.charger_releves(), exemple_releves())

    # suppression du dernier relevé : upsert puis DELETE des absents
    s.sauvegarder_releves(exemple_releves().iloc[:-1])
    assert len(s.charger_releves()) == 1
    s.sauvegarder_releves(releves.vide())
    assert s.charger_releves().empty

    s.sauvegarder_appareils(appareils.CATALOGUE.head(3))
    s.sauvegarder_appareils(appareils.CATALOGUE.head(2))
    assert len(s.charger_appareils()) == 2

    s.sauvegarder_tarifs(HistoriqueTarifs([GRILLE_DEFAUT]))
    assert s.charger_tarifs().grilles == [GRILLE_DEFAUT]

    s.sauvegarder_reglages({"prm": "1"})
    assert s.charger_reglages()["prm"] == "1"

    verifier_journal(s)
    assert api.tables["utilisations"] == []

    entetes = api.requetes[0][3]
    assert entetes["apikey"] == "sb_secret_abc" and "Authorization" not in entetes


def test_liste_in_echappe_les_guillemets():
    assert stockage._liste_in(['Lave-linge (40 °C)', 'a"b']) == '("Lave-linge (40 °C)","a\\"b")'


def test_choisir(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert isinstance(stockage.choisir({}), stockage.StockageLocal)
    s = stockage.choisir({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SECRET_KEY": "k"})
    assert isinstance(s, stockage.StockageSupabase)


def verifier_journal(s):
    """Ajout, arrêt et suppression d'utilisations, identiques pour les deux stockages."""
    assert s.charger_journal().empty
    u = jr.nouvelle("Plaques de cuisson", datetime(2026, 10, 20, 19, 0))
    s.ajouter_utilisation(u)
    s.ajouter_utilisation(jr.nouvelle("Lave-linge", datetime(2026, 10, 20, 22, 0), datetime(2026, 10, 21, 0, 0)))
    j = s.charger_journal()
    assert len(j) == 2 and pd.isna(j.iloc[0]["fin"])
    s.modifier_utilisation(u["id"], fin=datetime(2026, 10, 20, 19, 45))
    j = s.charger_journal()
    assert j.set_index("id").loc[u["id"], "fin"] == pd.Timestamp("2026-10-20 19:45")
    s.supprimer_utilisation(u["id"])
    j = s.charger_journal()
    assert list(j["appareil"]) == ["Lave-linge"]
    s.supprimer_utilisation(j.iloc[0]["id"])
