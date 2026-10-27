"""Gestion de la consommation électrique en heures creuses (tarif bleu).

Lancer avec : streamlit run app.py
"""

import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from hc import appareils, calculs, enedis, releves, stockage
from hc.demo import courbe_demo
from hc.tarifs import (
    GRILLE_DEFAUT, HistoriqueTarifs, Offre, format_plages, offres_depuis_grille, parse_plages,
)

st.set_page_config(page_title="Heures creuses", page_icon="⚡", layout="wide")

COULEURS = {"HC": "#2a9d8f", "HP": "#e76f51"}

SOURCE_DEMO = "Démo (données fictives)"
SOURCE_RELEVES = "Relevés manuels"
SOURCE_CSV = "Fichier CSV (courbe de charge)"
SOURCE_API = "Enedis via Conso API"


def euros(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def pourcent(x: float | None) -> str:
    return "–" if x is None or pd.isna(x) else f"{x:.0%}"


def lire_secrets() -> dict:
    try:
        return dict(st.secrets)
    except Exception:  # pas de fichier secrets.toml : usage local
        return {}


def lire_token() -> str:
    return lire_secrets().get("CONSO_API_TOKEN") or os.environ.get("CONSO_API_TOKEN", "")


@st.cache_data(ttl=3600, show_spinner="Récupération des données Enedis…")
def courbe_api(prm: str, token: str, debut: date, fin: date) -> tuple[pd.DataFrame, list[str]]:
    return enedis.recuperer_courbe(prm, token, debut, fin)


# --- Réglages ---------------------------------------------------------------

store = stockage.choisir(lire_secrets())
try:
    reglages = store.charger_reglages()
    historique = store.charger_tarifs()
    releves_df = store.charger_releves()
    mes_appareils = store.charger_appareils()
except Exception as exc:  # base injoignable ou clé invalide
    st.error(f"Impossible de lire les données ({store.nom}) : {exc}")
    st.stop()
grille = historique.actuelle()
try:
    plages = parse_plages(reglages["plages_hc"])
except ValueError as exc:
    st.error(f"{exc}. Corrigez vos plages dans l'onglet Réglages.")
    plages = parse_plages(stockage.REGLAGES_DEFAUT["plages_hc"])

def memoriser_prm(nouveau: str) -> None:
    """Enregistre le PRM sans formulaire ; une erreur d'écriture n'empêche pas l'affichage."""
    if nouveau == reglages.get("prm"):
        return
    reglages["prm"] = nouveau
    try:
        store.sauvegarder_reglages(reglages)
    except Exception as exc:  # base injoignable : on garde le PRM pour cette session
        st.toast(f"PRM non enregistré : {exc}")


token = lire_token()
prms_token = enedis.prms_du_token(token) if token else []
# PRM : celui des réglages s'il fait partie de ceux autorisés par le token,
# sinon le premier du token, sinon un éventuel secret CONSO_API_PRM
prm = reglages.get("prm", "")
if prms_token and prm not in prms_token:
    prm = prms_token[0]
prm = prm or lire_secrets().get("CONSO_API_PRM", "")
if prm:
    memoriser_prm(prm)

st.title("⚡ Mes heures creuses")
st.caption(
    f"Plages HC : **{format_plages(plages)}** · Prix HP {grille.prix_hp:.4f} €/kWh · "
    f"HC {grille.prix_hc:.4f} €/kWh (grille du {grille.date_debut:%d/%m/%Y})"
)

# --- Source de données ------------------------------------------------------

with st.sidebar:
    st.caption(f"💾 Données enregistrées dans : {store.nom}")
    st.header("Source des données")
    # Avec un token configuré, on ouvre directement sur les données Enedis
    source = st.radio("Source", [SOURCE_DEMO, SOURCE_RELEVES, SOURCE_CSV, SOURCE_API],
                      index=3 if token else 0, label_visibility="collapsed")

    courbe = None
    if source == SOURCE_DEMO:
        nb_jours = st.slider("Nombre de jours", 7, 60, 21)
        courbe = courbe_demo(nb_jours)
        st.info("Courbe fictive d'un appartement, pour découvrir l'application.")
    elif source == SOURCE_CSV:
        fichier = st.file_uploader("CSV : date ; puissance (W)", type=["csv", "txt"])
        if fichier:
            try:
                courbe = enedis.lire_csv(fichier)
            except (ValueError, KeyError) as exc:
                st.error(f"Lecture impossible : {exc}")
    elif source == SOURCE_API:
        if prm:
            st.caption(f"PRM : {prm}" + (" (lu dans votre token)" if prm in prms_token else ""))
        else:
            saisi = st.text_input("PRM (14 chiffres)", help="Enregistré automatiquement dès qu'il est complet.")
            if len(saisi.strip()) == 14 and saisi.strip().isdigit():
                memoriser_prm(saisi.strip())
                st.rerun()
        hier = date.today() - timedelta(days=1)
        periode = st.date_input("Période", (hier - timedelta(days=6), hier), max_value=hier)
        if not token:
            st.warning(
                "Token Conso API absent.\n\n"
                "1. Sur [conso.boris.sh](https://conso.boris.sh), donnez votre accord sur Enedis "
                "et copiez le token obtenu.\n"
                "2. Ajoutez la ligne `CONSO_API_TOKEN = \"votre-token\"` dans les secrets : "
                "Streamlit Cloud → Settings → Secrets (ou `.streamlit/secrets.toml` en local)."
            )
        elif not prm:
            st.info("Saisissez votre PRM : il sera mémorisé pour les prochaines fois.")
        elif isinstance(periode, tuple) and len(periode) == 2:
            try:
                courbe, erreurs_api = courbe_api(prm, token, periode[0], periode[1] + timedelta(days=1))
                if erreurs_api:
                    st.warning("Certaines périodes n'ont pas pu être récupérées (souvent : antérieures "
                               "à l'activation de la collecte) :\n\n- " + "\n- ".join(erreurs_api))
                if courbe.empty:
                    st.info("Aucune donnée sur cette période pour l'instant.")
            except Exception as exc:  # erreurs réseau ou HTTP renvoyées par l'API
                st.error(f"Erreur Conso API : {exc}")
                if "Enedis API returned an error" in str(exc):
                    st.info("Votre token et votre PRM sont acceptés : c'est Enedis qui n'a pas encore de données. "
                            "Juste après l'activation de la collecte, il faut en général attendre 1 à 3 jours "
                            "que la première journée complète soit disponible. En attendant, utilisez les "
                            "relevés manuels ou la démo.")
                st.caption("Causes fréquentes : période antérieure à l'activation de la collecte "
                           "(essayez seulement les derniers jours), collecte horaire pas encore activée, "
                           "token mal copié ou expiré, PRM différent de celui autorisé, "
                           "ou dates trop récentes (données disponibles à J+1).")

if courbe is not None and not courbe.empty:
    energie = calculs.energie_par_intervalle(courbe)
    classe = calculs.classer_hp_hc(energie, plages)
    bilan = calculs.bilan_journalier(classe, historique)
elif source == SOURCE_RELEVES:
    classe = None
    bilan = releves.bilan_journalier(releves_df, historique)
else:
    classe, bilan = None, pd.DataFrame()

onglets = st.tabs(["📊 Tableau de bord", "📝 Relevés", "💡 Optimisation", "🧺 Appareils & rappels", "⚙️ Réglages"])

# --- Tableau de bord ----------------------------------------------------------

with onglets[0]:
    if bilan.empty:
        if source == SOURCE_RELEVES:
            st.info("Saisissez au moins deux relevés dans l'onglet **Relevés** pour voir vos consommations.")
        else:
            st.info("Aucune donnée pour l'instant : choisissez une source dans le menu de gauche.")
    else:
        kwh_hp, kwh_hc = bilan["kwh_hp"].sum(), bilan["kwh_hc"].sum()
        part_hc = kwh_hc / (kwh_hp + kwh_hc) if kwh_hp + kwh_hc else None
        cout = bilan["cout_energie"].sum() + bilan["abonnement_jour"].sum()
        estim = calculs.estimation_facture(bilan, date.today())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Consommation", f"{kwh_hp + kwh_hc:.0f} kWh", f"{len(bilan)} jours", delta_color="off")
        c2.metric("Part en heures creuses", pourcent(part_hc),
                  "objectif > 50 %" if part_hc is not None and part_hc < 0.5 else "bien joué",
                  delta_color="off")
        c3.metric("Coût sur la période", euros(cout), "abonnement compris", delta_color="off")
        c4.metric("Projection annuelle", euros(estim["annee_projection"]),
                  f"≈ {estim['kwh_annee_projection']:.0f} kWh/an", delta_color="off")
        if pd.Timestamp(bilan["jour"].max()).date() >= date.today().replace(day=1):
            st.caption(
                f"Mois en cours : {euros(estim['mois_deja_consomme'])} déjà consommés, "
                f"estimation fin de mois **{euros(estim['mois_projection'])}** "
                f"(sur la base des {estim['jours_reference']} derniers jours)."
            )

        jours = bilan.melt(id_vars="jour", value_vars=["kwh_hc", "kwh_hp"], var_name="periode", value_name="kWh")
        jours["periode"] = jours["periode"].map({"kwh_hc": "HC", "kwh_hp": "HP"})
        fig = px.bar(jours, x="jour", y="kWh", color="periode", color_discrete_map=COULEURS,
                     title="Consommation par jour")
        fig.update_layout(legend_title_text="", xaxis_title="")
        st.plotly_chart(fig, width="stretch")

        if classe is not None:
            limite = reglages["puissance_souscrite_kva"] * 1000
            fig = go.Figure(go.Bar(x=bilan["jour"], y=bilan["puissance_max_w"], name="Puissance max (moy. 30 min)",
                                   marker_color="#264653"))
            fig.add_hline(y=limite, line_dash="dash", line_color="red",
                          annotation_text=f"{reglages['puissance_souscrite_kva']} kVA")
            fig.update_layout(title="Pic de puissance par jour", yaxis_title="W")
            st.plotly_chart(fig, width="stretch")
            st.caption("Moyennes sur 30 min : les pics réels (quelques secondes) sont plus élevés. "
                       "Au-dessus de ~70 % de votre puissance souscrite, attention aux appareils lancés en même temps.")

# --- Relevés -----------------------------------------------------------------

with onglets[1]:
    st.subheader("Saisir un relevé du compteur")
    st.caption("Sur le Linky, appuyez sur « + » jusqu'aux index HEURES CREUSES et HEURES PLEINES.")
    with st.form("releve", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        jour = c1.date_input("Date", date.today(), max_value=date.today())
        heure = c2.time_input("Heure", datetime.now().time().replace(second=0, microsecond=0))
        index_hc = c3.number_input("Index HC (kWh)", min_value=0.0, step=1.0, format="%.0f")
        index_hp = c4.number_input("Index HP (kWh)", min_value=0.0, step=1.0, format="%.0f")
        if st.form_submit_button("Enregistrer"):
            moment = datetime.combine(jour, heure)
            erreurs = releves.verifier_nouveau(releves_df, moment, index_hc, index_hp)
            if erreurs:
                for e in erreurs:
                    st.error(e)
            else:
                store.sauvegarder_releves(releves.ajouter(releves_df, moment, index_hc, index_hp))
                st.success("Relevé enregistré.")
                st.rerun()

    if releves_df.empty:
        st.info("Aucun relevé pour l'instant.")
    else:
        per = releves.periodes(releves_df, historique)
        if not per.empty:
            st.dataframe(
                per.assign(
                    debut=per["debut"].dt.strftime("%d/%m %H:%M"), fin=per["fin"].dt.strftime("%d/%m %H:%M"),
                    part_hc=per["part_hc"].map(pourcent),
                    cout=(per["cout_energie"] + per["abonnement"]).map(euros),
                )[["debut", "fin", "kwh_hc", "kwh_hp", "part_hc", "cout"]].rename(columns={
                    "debut": "Du", "fin": "Au", "kwh_hc": "kWh HC", "kwh_hp": "kWh HP",
                    "part_hc": "Part HC", "cout": "Coût"}),
                hide_index=True, width="stretch",
            )
        with st.expander(f"Tous les relevés ({len(releves_df)})"):
            st.dataframe(releves_df, hide_index=True, width="stretch")
            if st.button("Supprimer le dernier relevé"):
                store.sauvegarder_releves(releves_df.iloc[:-1])
                st.rerun()
        if source != SOURCE_RELEVES:
            st.caption("Choisissez « Relevés manuels » dans le menu de gauche pour les utiliser dans le tableau de bord.")

# --- Optimisation -------------------------------------------------------------

with onglets[2]:
    if bilan.empty:
        st.info("Il faut des données pour les conseils : démo, relevés, CSV ou Conso API.")
    else:
        ecart = grille.prix_hp - grille.prix_hc
        if classe is not None:
            st.subheader("Consommation de fond (talon)")
            talon = calculs.talon_w(classe)
            prix_moyen = (bilan["cout_energie"].sum() / bilan["kwh_total"].sum()) if bilan["kwh_total"].sum() else grille.prix_hp
            c1, c2 = st.columns(2)
            c1.metric("Talon", f"{talon:.0f} W")
            c2.metric("Coût annuel du talon", euros(calculs.cout_annuel_talon(talon, prix_moyen)))
            st.caption("Ce qui tourne en permanence : box, réfrigérateur, veilles, VMC… "
                       "Chaque tranche de 10 W en moins économise environ "
                       f"{euros(calculs.cout_annuel_talon(10, prix_moyen))} par an.")

            st.subheader("Profil de la journée type")
            profil = calculs.profil_horaire(classe)
            fig = px.bar(profil, x="heure", y="puissance_w", color="periode", color_discrete_map=COULEURS)
            fig.update_layout(xaxis_title="", yaxis_title="Puissance moyenne (W)", legend_title_text="")
            st.plotly_chart(fig, width="stretch")

            st.subheader("Grosses consommations en heures pleines")
            seuil = st.number_input("Seuil (W)", 300, 6000, int(reglages["seuil_pic_w"]), step=100)
            pics = calculs.pics_heures_pleines(classe, seuil, ecart)
            if pics.empty:
                st.success("Aucune consommation au-dessus du seuil en heures pleines. 👍")
            else:
                nb_jours_courbe = classe["debut"].dt.date.nunique()
                st.write(f"Déplacer ces consommations en heures creuses ferait gagner environ "
                         f"**{euros(pics['gain_possible'].sum() * 365 / nb_jours_courbe)} par an**.")
                st.dataframe(
                    pics.head(10).assign(
                        nb_jours=pics["nb_jours"].astype(str) + f" / {nb_jours_courbe}",
                        gain_possible=pics["gain_possible"].map(euros),
                    ).rename(columns={"heure": "Créneau", "nb_jours": "Jours concernés", "kwh": "kWh",
                                      "gain_possible": "Gain si déplacé"}),
                    hide_index=True, width="stretch",
                )
                st.caption("Une forte consommation à la même heure presque tous les jours en HP peut indiquer "
                           "un ballon d'eau chaude qui ne chauffe pas uniquement en heures creuses "
                           "(contacteur jour/nuit à vérifier sur le tableau électrique).")

        st.subheader("Simulateur d'offres")
        offres = offres_depuis_grille(grille, plages)
        with st.expander("Ajouter une offre à comparer (autre fournisseur, futures plages HC…)"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nom", "Offre personnalisée")
            abo = c2.number_input("Abonnement (€/mois TTC)", 0.0, 100.0, 0.0, step=0.5)
            c1, c2, c3 = st.columns(3)
            p_hp = c1.number_input("Prix HP ou Base (€/kWh)", 0.0, 1.0, 0.0, step=0.001, format="%.4f")
            p_hc = c2.number_input("Prix HC (€/kWh)", 0.0, 1.0, 0.0, step=0.001, format="%.4f")
            pl_txt = c3.text_input("Plages HC (vide = option Base)", "")
            if abo > 0 and p_hp > 0:
                try:
                    pl = parse_plages(pl_txt) if pl_txt.strip() else []
                    offres.append(Offre(nom, abo, p_hp, p_hc if pl else 0.0, pl))
                except ValueError as exc:
                    st.error(str(exc))

        if classe is not None:
            resultats = [calculs.cout_offre(energie, o) for o in offres]
        else:
            nb_j = len(bilan)
            resultats = calculs.comparer_offres_kwh(bilan["kwh_hp"].sum(), bilan["kwh_hc"].sum(), nb_j, offres)
            st.caption("Avec des relevés manuels, une offre avec d'autres plages est simulée avec votre répartition actuelle.")
        res = pd.DataFrame(resultats).sort_values("total_annuel")
        fig = px.bar(res, x="total_annuel", y="offre", orientation="h", text=res["total_annuel"].map(euros))
        fig.update_layout(xaxis_title="Coût annuel estimé (€)", yaxis_title="", yaxis={"categoryorder": "total descending"})
        st.plotly_chart(fig, width="stretch")

        kwh_an = bilan["kwh_total"].mean() * 365
        seuil_hc = calculs.part_hc_rentable(grille.abonnement_hc_mensuel, grille.prix_hp, grille.prix_hc,
                                            grille.abonnement_base_mensuel, grille.prix_base, kwh_an)
        part_actuelle = bilan["kwh_hc"].sum() / bilan["kwh_total"].sum()
        if seuil_hc is None:
            st.warning("Avec ces prix, l'option Heures creuses n'est jamais plus avantageuse que Base.")
        else:
            st.info(f"Pour ~{kwh_an:.0f} kWh/an, l'option HC est rentable au-delà de **{seuil_hc:.0%}** "
                    f"de consommation en heures creuses. Vous êtes à **{part_actuelle:.0%}**.")

# --- Appareils & rappels --------------------------------------------------------

with onglets[3]:
    st.subheader("Mes appareils programmables")
    if mes_appareils is None:
        st.info("Quels appareils avez-vous ? Cochez-les : des valeurs typiques sont pré-remplies, "
                "vous pourrez ensuite les ajuster et ajouter les vôtres.")
        catalogue = appareils.CATALOGUE
        coches = [nom for nom in catalogue["nom"] if st.checkbox(nom, key=f"cat_{nom}")]
        if st.button("Créer ma liste", type="primary", disabled=not coches):
            store.sauvegarder_appareils(catalogue[catalogue["nom"].isin(coches)])
            st.rerun()
        liste = catalogue.iloc[0:0]
    else:
        liste = st.data_editor(
            mes_appareils, num_rows="dynamic", hide_index=True, width="stretch",
            column_config={
                "nom": "Appareil",
                "kwh_cycle": st.column_config.NumberColumn("kWh par cycle", min_value=0.0, format="%.2f"),
                "duree_h": st.column_config.NumberColumn("Durée (h)", min_value=0.0, format="%.1f"),
                "cycles_semaine": st.column_config.NumberColumn("Cycles / semaine", min_value=0, step=1),
            },
        )
        st.caption("Modifiez directement le tableau, ajoutez une ligne en bas ou supprimez-en une, puis enregistrez. "
                   "Le kWh par cycle figure sur l'étiquette énergie ou la notice (souvent indiqué pour 100 cycles).")
        if st.button("Enregistrer mes appareils"):
            try:
                store.sauvegarder_appareils(liste)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Appareils enregistrés.")

    plan = appareils.plan_appareils(liste, plages, grille.prix_hp, grille.prix_hc)
    if not plan.empty:
        st.dataframe(
            plan.assign(**{c: plan[c].map(euros) for c in ("Gain par cycle (€)", "Gain par an (€)")}),
            hide_index=True, width="stretch",
        )
        st.write(f"Gain total si tous ces cycles passent d'HP en HC : **{euros(plan['Gain par an (€)'].sum())} par an**.")

    st.subheader("Rappels dans mon agenda")
    minutes = st.slider("Me prévenir … minutes avant le début des heures creuses", 0, 60, 10, step=5)
    st.download_button(
        "📅 Télécharger les rappels (.ics)",
        appareils.calendrier_rappels(plages, minutes),
        file_name="rappels_heures_creuses.ics", mime="text/calendar",
    )
    st.caption("Ouvrez ce fichier sur votre téléphone ou importez-le dans Google Agenda / Apple Calendrier / Outlook : "
               "vous recevrez une notification chaque jour. À refaire si vos plages HC changent.")

# --- Réglages -----------------------------------------------------------------

with onglets[4]:
    st.subheader("Mon contrat")
    with st.form("reglages"):
        plages_txt = st.text_input(
            "Plages d'heures creuses", reglages["plages_hc"],
            help="Format HH:MM-HH:MM, séparées par « ; ». Ex. : 01:30-07:30; 12:30-14:30. "
                 "Vos plages exactes figurent sur votre facture ou votre espace Enedis.",
        )
        total_hc = sum(p.duree_heures() for p in plages)
        st.caption(f"Plages actuelles : {format_plages(plages)}, soit {total_hc:g} h d'heures creuses par jour"
                   + ("." if total_hc == 8 else " (en général 8 h : vérifiez sur votre facture ou votre espace Enedis)."))
        c1, c2, c3 = st.columns(3)
        prm_saisi = c1.text_input("PRM (n° du compteur)", reglages["prm"])
        kva = c2.selectbox("Puissance souscrite (kVA)", [3, 6, 9, 12, 15, 18],
                           index=[3, 6, 9, 12, 15, 18].index(reglages["puissance_souscrite_kva"]))
        seuil_pic = c3.number_input("Seuil « grosse consommation » (W)", 300, 6000, int(reglages["seuil_pic_w"]), step=100)
        if st.form_submit_button("Enregistrer"):
            try:
                parse_plages(plages_txt)
            except ValueError as exc:
                st.error(str(exc))
            else:
                store.sauvegarder_reglages({**reglages, "plages_hc": plages_txt, "prm": prm_saisi.strip(),
                                            "puissance_souscrite_kva": kva, "seuil_pic_w": seuil_pic})
                st.success("Réglages enregistrés.")
                st.rerun()

    st.subheader("Historique des tarifs (TTC)")
    st.caption("Ajoutez une ligne à chaque changement de prix : chaque grille s'applique jusqu'à la suivante, "
               "les coûts passés restent donc justes. Les valeurs par défaut sont indicatives, "
               "vérifiez-les sur votre facture.")
    tarifs_df = st.data_editor(
        historique.vers_dataframe(), num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "date_debut": st.column_config.DateColumn("À partir du", format="DD/MM/YYYY"),
            "abonnement_hc_mensuel": st.column_config.NumberColumn("Abo HC (€/mois)", format="%.2f"),
            "prix_hp": st.column_config.NumberColumn("Prix HP (€/kWh)", format="%.4f"),
            "prix_hc": st.column_config.NumberColumn("Prix HC (€/kWh)", format="%.4f"),
            "abonnement_base_mensuel": st.column_config.NumberColumn("Abo Base (€/mois)", format="%.2f"),
            "prix_base": st.column_config.NumberColumn("Prix Base (€/kWh)", format="%.4f"),
        },
    )
    c1, c2 = st.columns(2)
    if c1.button("Enregistrer les tarifs"):
        try:
            store.sauvegarder_tarifs(HistoriqueTarifs.depuis_dataframe(tarifs_df))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Tarifs enregistrés.")
            st.rerun()
    if c2.button("Revenir aux valeurs par défaut"):
        store.sauvegarder_tarifs(HistoriqueTarifs([GRILLE_DEFAUT]))
        st.rerun()
