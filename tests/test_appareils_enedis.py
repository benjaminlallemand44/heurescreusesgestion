from datetime import date

import pandas as pd
import pytest

from hc import appareils, enedis
from hc.tarifs import parse_plages


def test_plan_appareils():
    plages = parse_plages("01:00-07:00; 12:00-14:00")
    plan = appareils.plan_appareils(appareils.CATALOGUE, plages, 0.30, 0.20)
    assert len(plan) == len(appareils.CATALOGUE)
    assert plan["Conseil"].iloc[0].startswith("Lancer à 01:00")
    lave_linge = appareils.CATALOGUE.iloc[0]
    assert plan["Gain par an (€)"].iloc[0] == pytest.approx(
        lave_linge["kwh_cycle"] * 0.10 * lave_linge["cycles_semaine"] * 52
    )


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
            def raise_for_status(self):
                pass

            def json(self):
                return {"interval_reading": [{"value": "100", "date": f"{params['start']} 00:30:00"}]}
        return Rep()


def test_recuperer_courbe_plusieurs_requetes():
    session = FausseSession()
    df = enedis.recuperer_courbe("12345678901234", "secret", date(2026, 10, 1), date(2026, 10, 10), session)
    assert len(session.appels) == 2
    assert len(df) == 2


def test_lire_csv(tmp_path):
    f = tmp_path / "courbe.csv"
    f.write_text("date;valeur\n2026-10-20 00:30;420\n2026-10-20 01:00;1850\n")
    df = enedis.lire_csv(f)
    assert list(df.columns) == ["date", "puissance_w"]
    assert df["puissance_w"].sum() == 2270
