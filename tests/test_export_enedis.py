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


def test_format_espace_enedis_debut_fin_kw(tmp_path):
    """Format réel de l'export horaire de l'espace client Enedis."""
    lignes = [["Début", "Fin", "Valeur (en kW)"],
              ["24/10/2026 23:00:00", "25/10/2026 00:00:00", "0,06"],
              ["25/10/2026 00:00:00", "25/10/2026 01:00:00", "0,08"],
              ["25/10/2026 01:00:00", "25/10/2026 02:00:00", "0,70"],
              # passage à l'heure d'hiver : la même heure apparaît deux fois
              ["25/10/2026 01:00:00", "25/10/2026 02:00:00", "1,67"],
              ["25/10/2026 02:00:00", "25/10/2026 03:00:00", "0,80"]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    # repère = fin d'intervalle, puissance en W
    assert list(df["date"]) == [pd.Timestamp(f"2026-10-25 0{h}:00") for h in range(4)]
    assert list(df["puissance_w"]) == pytest.approx([60, 80, 2370, 800])


def test_format_espace_enedis_dates_excel(tmp_path):
    lignes = [["Début", "Fin", "Valeur (en kW)"],
              [datetime(2026, 10, 19, 0), datetime(2026, 10, 19, 1), 0.07],
              [datetime(2026, 10, 19, 1), datetime(2026, 10, 19, 2), 0.49]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    assert list(df["date"]) == [pd.Timestamp("2026-10-19 01:00"), pd.Timestamp("2026-10-19 02:00")]
    assert list(df["puissance_w"]) == pytest.approx([70, 490])


def test_colonne_debut_seule_decalee_d_un_pas(tmp_path):
    lignes = [["Début", "Valeur (en W)"], ["19/10/2026 00:00", 100], ["19/10/2026 00:30", 200]]
    df = lire_export(xlsx(tmp_path, lignes), "export.xlsx")
    assert df["date"].iloc[0] == pd.Timestamp("2026-10-19 00:30")
