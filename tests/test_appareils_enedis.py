from datetime import date

import pandas as pd
import pytest

from hc import appareils, enedis
from hc.tarifs import parse_plages


def test_plan_appareils():
    plages = parse_plages("01:00-07:00; 12:00-14:00")
    plan = appareils.plan_appareils(appareils.CATALOGUE, plages, 0.30, 0.20)
    deplacables = appareils.CATALOGUE[appareils.CATALOGUE["deplacable"]]
    assert list(plan["Appareil"]) == list(deplacables["nom"])
    lave_linge = plan[plan["Appareil"] == "Lave-linge"].iloc[0]
    assert lave_linge["Conseil"].startswith("Lancer à 01:00")
    assert lave_linge["Gain par an (€)"] == pytest.approx(0.9 * 0.10 * 3 * 52)
    # sèche-serviettes : 6 mois par an
    seche = plan[plan["Appareil"] == "Sèche-serviettes"].iloc[0]
    assert seche["Gain par an (€)"] == pytest.approx(0.75 * 1.5 * 0.10 * 7 * 52 / 2)


def test_kwh_annuel_selon_usage():
    cat = appareils.bilan_inventaire(appareils.CATALOGUE).set_index("nom")
    assert cat.loc["Réfrigérateur-congélateur", "kwh_an"] == pytest.approx(35 * 8.76)
    assert cat.loc["Lave-linge", "kwh_utilisation"] == pytest.approx(0.9)
    assert cat.loc["Radiateur séjour", "kwh_an"] == pytest.approx(1.5 * 5 * 7 * 52 / 2)


def test_normaliser_ancienne_liste():
    ancienne = pd.DataFrame([{"nom": "Lave-linge (40 °C)", "kwh_cycle": 0.9, "duree_h": 2.0, "cycles_semaine": 3}])
    df = appareils.normaliser(ancienne)
    assert list(df.columns) == appareils.COLONNES
    ligne = df.iloc[0]
    assert ligne["usage"] == "cycle" and ligne["deplacable"] and ligne["utilisations_semaine"] == 3


def test_cycle_trop_long():
    plages = parse_plages("12:00-14:00")
    assert appareils.meilleur_creneau(plages, 3) is None


def test_calendrier_rappels():
    ics = appareils.calendrier_rappels(parse_plages("22:00-06:00; 12:30-14:30"), 10, date(2026, 11, 1))
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 2
    assert "DTSTART;TZID=Europe/Paris:20261101T220000" in ics
    assert "TRIGGER:-PT10M" in ics
    assert "RRULE:FREQ=DAILY" in ics


def test_parse_courbe_conso_api():
    reponse = {"interval_reading": [
        {"value": "420", "date": "2026-10-20 00:30:00"},
        {"value": "1850", "date": "2026-10-20 01:00:00"},
    ]}
    df = enedis.parse_courbe(reponse)
    assert list(df["puissance_w"]) == [420, 1850]
    assert df["date"].iloc[1] == pd.Timestamp("2026-10-20 01:00")


def test_decouper_periode():
    tranches = enedis.decouper_periode(date(2026, 10, 1), date(2026, 10, 16))
    assert tranches[0] == (date(2026, 10, 1), date(2026, 10, 8))
    assert tranches[-1] == (date(2026, 10, 15), date(2026, 10, 16))
    assert len(tranches) == 3


class FausseSession:
    def __init__(self):
        self.appels = []

    def get(self, url, params, headers, timeout):
        self.appels.append(params)
        assert headers["Authorization"] == "Bearer secret"

        class Rep:
            ok = True

            def json(self):
                return {"interval_reading": [{"value": "100", "date": f"{params['start']} 00:30:00"}]}
        return Rep()


def test_recuperer_courbe_plusieurs_requetes():
    session = FausseSession()
    df, erreurs = enedis.recuperer_courbe("12345678901234", "secret", date(2026, 10, 1), date(2026, 10, 10), session)
    assert len(session.appels) == 2
    assert len(df) == 2
    assert erreurs == []


def test_lire_csv(tmp_path):
    f = tmp_path / "courbe.csv"
    f.write_text("date;valeur\n2026-10-20 00:30;420\n2026-10-20 01:00;1850\n")
    df = enedis.lire_csv(f)
    assert list(df.columns) == ["date", "puissance_w"]
    assert df["puissance_w"].sum() == 2270


class SessionErreur:
    def get(self, url, params, headers, timeout):
        class Rep:
            ok = False
            status_code = 400
            reason = "Bad Request"
            text = ""

            def json(self):
                return {"status": 400, "message": "Aucune donnée pour ce PRM"}
        return Rep()


def test_erreur_conso_api_affiche_le_message():
    with pytest.raises(enedis.ErreurConsoAPI, match="Aucune donnée pour ce PRM"):
        enedis.recuperer_courbe("12345678901234", "t", date(2026, 10, 1), date(2026, 10, 2), SessionErreur())


@pytest.mark.parametrize("brut", ["abc", " abc\n", '"abc"', "Bearer abc", "  bearer abc "])
def test_nettoyer_token(brut):
    assert enedis.nettoyer_token(brut) == "abc"


class SessionPremiereTrancheRefusee(FausseSession):
    def get(self, url, params, headers, timeout):
        if params["start"] == "2026-10-11":
            return SessionErreur().get(url, params, headers, timeout)
        return super().get(url, params, headers, timeout)


def test_tranche_refusee_n_empeche_pas_les_suivantes():
    session = SessionPremiereTrancheRefusee()
    df, erreurs = enedis.recuperer_courbe("12345678901234", "secret", date(2026, 10, 11), date(2026, 10, 26), session)
    assert len(df) == 2  # tranches du 18 et du 25
    assert len(erreurs) == 1 and "du 11/10 au 18/10" in erreurs[0] and "Aucune donnée" in erreurs[0]


def jwt(contenu: dict) -> str:
    import base64
    import json
    corps = base64.urlsafe_b64encode(json.dumps(contenu).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{corps}.signature"


def test_prms_du_token():
    assert enedis.prms_du_token(jwt({"sub": ["14471635251218"], "exp": 1})) == ["14471635251218"]
    assert enedis.prms_du_token(jwt({"sub": "12345678901234"})) == ["12345678901234"]
    assert enedis.prms_du_token("Bearer " + jwt({"sub": ["11111111111111", "22222222222222"]})) == [
        "11111111111111", "22222222222222"]
    assert enedis.prms_du_token(jwt({"exp": 12345678901234})) == ["12345678901234"]
    assert enedis.prms_du_token("pas-un-jwt") == []
    assert enedis.prms_du_token("a.!!!.b") == []


def test_message_erreur_inclut_les_details_enedis():
    class Rep:
        def json(self):
            return {"status": 400, "message": "The Enedis API returned an error",
                    "enedis": {"error": "ADAM-ERR0123", "error_description": "no measure"}}
    message = enedis._message_erreur(Rep())
    assert message.startswith("The Enedis API returned an error")
    assert "ADAM-ERR0123" in message and "no measure" in message


def test_explication_erreur_titulaire():
    message = ('du 26/10 au 29/10 : 400 — The Enedis API returned an error {"error": {"error": "ADAM-DC-0007", '
               '"error_description": "Le client n\'est pas titulaire du point demandé."}}')
    assert "titulaire" in enedis.explication_erreur(message)
    assert enedis.explication_erreur("erreur inconnue") is None
