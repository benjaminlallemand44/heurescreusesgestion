from datetime import datetime

import openpyxl
import pandas as pd
import pytest

from hc.export_enedis import lire_export

INFOS = [
    ["Identifiant PRM", "Type de données", "Date de début", "Date de fin", "Grandeur physique", "Grandeur métier",
     "Etape métier", "Unité", "Pas en minutes"],
    ["14471635251218", "Courbe de charge", "26/10/2026", "28/10/2026", "Energie active", "Consommation",
     "Comptage Brut", "W", "30"],
]


def xlsx(tmp_path, lignes, nom="export.xlsx"):
    classeur = openpyxl.Workbook()
    feuille = classeur.active
    for ligne in lignes:
        feuille.append(ligne)
    chemin = tmp_path / nom
    classeur.save(chemin)
    return chemin


def test_xlsx_avec_lignes_d_informations(tmp_path):
    lignes = INFOS + [[], ["Horodate", "Valeur"],
                      [datetime(2026, 10, 26, 0, 30), 420], [datetime(2026, 10, 26, 1, 0), 1850],
                      [datetime(2026, 10, 26, 1, 30), 1800]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    assert list(df.columns) == ["date", "puissance_w"]
    assert list(df["puissance_w"]) == [420, 1850, 1800]
    assert df["date"].iloc[0] == pd.Timestamp("2026-10-26 00:30")


def test_xlsx_dates_texte_iso_avec_changement_d_heure(tmp_path):
    lignes = [["Horodate", "Valeur"],
              ["2026-10-25T01:30:00+02:00", "300"], ["2026-10-25T02:00:00+02:00", "310"],
              ["2026-10-25T02:30:00+01:00", "320"]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    assert len(df) == 3 and df["date"].dt.tz is None
    assert df["date"].iloc[0] == pd.Timestamp("2026-10-25 01:30")


def test_unite_wh_convertie_en_puissance(tmp_path):
    infos = [r[:] for r in INFOS]
    infos[1][7] = "Wh"
    lignes = infos + [["Horodate", "Valeur"],
                      ["26/10/2026 00:30", "250"], ["26/10/2026 01:00", "500"]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    assert list(df["puissance_w"]) == [500, 1000]  # 250 Wh en 30 min = 500 W


def test_csv_enedis_point_virgule(tmp_path):
    texte = ";".join(INFOS[0]) + "\n" + ";".join(INFOS[1]) + "\nHorodate;Valeur\n" \
        "2026-10-26T00:30:00+01:00;420\n2026-10-26T01:00:00+01:00;1850\n"
    chemin = tmp_path / "export.csv"
    chemin.write_text(texte, encoding="latin-1")
    df = lire_export(chemin, "export.csv")
    assert list(df["puissance_w"]) == [420, 1850]


def test_csv_simple(tmp_path):
    chemin = tmp_path / "courbe.csv"
    chemin.write_text("date;valeur\n2026-10-20 00:30;420\n2026-10-20 01:00;1850\n")
    assert lire_export(chemin, "courbe.csv")["puissance_w"].sum() == 2270


def test_fichier_journalier_refuse(tmp_path):
    lignes = [["Date", "Valeur"], [datetime(2026, 10, 26), 9000], [datetime(2026, 10, 27), 8000]]
    with pytest.raises(ValueError, match="par jour"):
        lire_export(xlsx(tmp_path, lignes), "export.xlsx")


def test_fichier_sans_tableau(tmp_path):
    with pytest.raises(ValueError, match="introuvable"):
        lire_export(xlsx(tmp_path, [["Bonjour"], ["rien ici"]]), "export.xlsx")
