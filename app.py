"""Gestion de la consommation électrique en heures creuses (tarif bleu).

Lancer avec : streamlit run app.py
"""

import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from hc import apprentissage, appareils, calculs, enedis, export_enedis, optimisation, releves, stockage
from hc import journal as jr
from hc.demo import courbe_demo
from hc.tarifs import (
    GRILLE_DEFAUT, HistoriqueTarifs, TarifsTempo, est_heure_creuse, format_plages, offres_depuis_grille,
    parse_plages,
)

st.set_page_config(page_title="Heures creuses", page_icon="⚡", layout="wide")

COULEURS = {"HC": "#2a9d8f", "HP": "#e76f51"}

SOURCE_DEMO = "Démo (données fictives)"
SOURCE_RELEVES = "Relevés manuels"
SOURCE_FICHIER = "Fichier exporté d'Enedis (.xlsx)"
SOURCE_API = "Enedis via Conso API"
# Jours de courbe récupérés pour apprendre à partir du journal d'utilisation
JOURS_APPRENTISSAGE = 60


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


# --- Données enregistrées ---------------------------------------------------

store = stockage.choisir(lire_secrets())
try:
    reglages = store.charger_reglages()
    historique = store.charger_tarifs()
    releves_df = store.charger_releves()
    mes_appareils = store.charger_appareils()
    journal_df = store.charger_journal()
except Exception as exc:  # base injoignable ou clé invalide
    st.error(f"Impossible de lire les données ({store.nom}) : {exc}")
    st.stop()
grille = historique.actuelle()
tempo = TarifsTempo.depuis_dict(reglages.get("tempo"))
inventaire = mes_appareils if mes_appareils is not None else appareils.normaliser(appareils.CATALOGUE.iloc[0:0])
aujourd_hui = jr.maintenant().date()
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

# --- Je lance un appareil (accès rapide, sur toutes les pages) ---------------

lancables = inventaire[inventaire["usage"] != "permanent"]
actifs = jr.en_cours(journal_df)
with st.expander(f"▶ Je lance un appareil{f' · {len(actifs)} en cours' if len(actifs) else ''}",
                 expanded=len(actifs) > 0):
    if lancables.empty:
        st.info("Ajoutez d'abord vos appareils dans l'onglet **🔌 Mes appareils**.")
    else:
        c1, c2 = st.columns([3, 1])
        choix = c1.selectbox("Appareil", lancables["nom"], label_visibility="collapsed")
        if c2.button("▶ Lancer", type="primary", width="stretch"):
            ligne = lancables[lancables["nom"] == choix].iloc[0]
            store.ajouter_utilisation(jr.lancer(ligne))
            st.toast(f"{choix} : noté à {jr.maintenant():%H:%M}")
            st.rerun()
        st.caption("Un appareil à programme (lave-linge…) s'arrête tout seul à la fin de sa durée habituelle ; "
                   "les autres (plaques, four, radiateur…) restent « en cours » jusqu'à ce que vous les arrêtiez.")
    for u in actifs.itertuples():
        c1, c2 = st.columns([3, 1])
        c1.write(f"⏳ **{u.appareil}** depuis {u.debut:%H:%M}")
        if c2.button("⏹ Arrêter", key=f"stop_{u.id}", width="stretch"):
            store.modifier_utilisation(u.id, fin=max(jr.maintenant(), u.debut + timedelta(minutes=1)))
            st.rerun()

# --- Source de données ------------------------------------------------------

with st.sidebar:
    st.caption(f"💾 Données enregistrées dans : {store.nom}")
    st.header("Source des données")
    # Avec un token configuré, on ouvre directement sur les données Enedis
    source = st.radio("Source", [SOURCE_DEMO, SOURCE_RELEVES, SOURCE_FICHIER, SOURCE_API],
                      index=3 if token else 0, label_visibility="collapsed")

    courbe = None
    courbe_apprentissage = None
    if source == SOURCE_DEMO:
        nb_jours = st.slider("Nombre de jours", 7, 60, 21)
        courbe = courbe_demo(nb_jours, fin=pd.Timestamp(aujourd_hui))
        st.info("Courbe fictive d'un appartement, pour découvrir l'application. "
                "L'apprentissage des appareils n'utilise que vos vraies données.")
    elif source == SOURCE_FICHIER:
        st.caption("Sur enedis.fr → Mon espace → votre consommation, affichez la consommation **horaire** "
                   "(par demi-heure), puis téléchargez-la. Déposez ici le fichier obtenu.")
        fichier = st.file_uploader("Fichier Enedis", type=["xlsx", "xls", "csv"], label_visibility="collapsed")
        if fichier:
            try:
                courbe = courbe_apprentissage = export_enedis.lire_export(fichier, fichier.name)
            except (ValueError, KeyError) as exc:
                st.error(f"Lecture impossible : {exc}")
            else:
                st.success(f"{len(courbe)} mesures, du {courbe['date'].min():%d/%m/%Y} "
                           f"au {courbe['date'].max():%d/%m/%Y}.")
    elif source == SOURCE_API:
        if prm:
            st.caption(f"PRM : {prm}" + (" (lu dans votre token)" if prm in prms_token else ""))
        else:
            saisi = st.text_input("PRM (14 chiffres)", help="Enregistré automatiquement dès qu'il est complet.")
            if len(saisi.strip()) == 14 and saisi.strip().isdigit():
                memoriser_prm(saisi.strip())
                st.rerun()
        hier = aujourd_hui - timedelta(days=1)
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
                if explication := enedis.explication_erreur(str(exc)):
                    st.info(explication)
                elif "Enedis API returned an error" in str(exc):
                    st.info("Votre token et votre PRM sont acceptés : c'est Enedis qui n'a pas encore de données. "
                            "Juste après l'activation de la collecte, il faut en général attendre 1 à 3 jours "
                            "que la première journée complète soit disponible. En attendant, utilisez les "
                            "relevés manuels ou la démo.")
                st.caption("Causes fréquentes : période antérieure à l'activation de la collecte "
                           "(essayez seulement les derniers jours), collecte horaire pas encore activée, "
                           "token mal copié ou expiré, PRM différent de celui autorisé, "
                           "ou dates trop récentes (données disponibles à J+1).")
            # Pour l'apprentissage : la courbe couvrant aussi les utilisations plus anciennes
            courbe_apprentissage = courbe
            if not journal_df.empty and token and prm:
                debut_journal = max(journal_df["debut"].min().date(), aujourd_hui - timedelta(days=JOURS_APPRENTISSAGE))
                if courbe is None or courbe.empty or debut_journal < courbe["date"].min().date():
                    try:
                        courbe_apprentissage, _ = courbe_api(prm, token, debut_journal, aujourd_hui)
                    except Exception:  # pas de données plus anciennes : on apprend sur la période affichée
                        pass

if courbe is not None and not courbe.empty:
    energie = calculs.energie_par_intervalle(courbe)
    classe = calculs.classer_hp_hc(energie, plages)
    bilan = calculs.bilan_journalier(classe, historique)
elif source == SOURCE_RELEVES:
    energie = classe = None
    bilan = releves.bilan_journalier(releves_df, historique)
else:
    energie = classe = None
    bilan = pd.DataFrame()

# Apprentissage : uniquement sur de vraies données
if courbe_apprentissage is not None and not courbe_apprentissage.empty:
    energie_apprentissage = calculs.energie_par_intervalle(courbe_apprentissage)
    obs = apprentissage.observations(energie_apprentissage, journal_df, inventaire)
else:
    energie_apprentissage = None
    obs = apprentissage.observations(pd.DataFrame(), journal_df, inventaire)
appris = apprentissage.apprendre(obs, inventaire)
talon = calculs.talon_w(energie) if energie is not None else None

onglets = st.tabs(["📊 Tableau de bord", "📅 Ma journée", "🔌 Mes appareils", "💡 Optimisation",
                   "📝 Relevés", "⚙️ Réglages"])

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
        estim = calculs.estimation_facture(bilan, aujourd_hui)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Consommation", f"{kwh_hp + kwh_hc:.0f} kWh", f"{len(bilan)} jours", delta_color="off")
        c2.metric("Part en heures creuses", pourcent(part_hc),
                  "objectif > 50 %" if part_hc is not None and part_hc < 0.5 else "bien joué",
                  delta_color="off")
        c3.metric("Coût sur la période", euros(cout), "abonnement compris", delta_color="off")
        c4.metric("Projection annuelle", euros(estim["annee_projection"]),
                  f"≈ {estim['kwh_annee_projection']:.0f} kWh/an", delta_color="off")
        if pd.Timestamp(bilan["jour"].max()).date() >= aujourd_hui.replace(day=1):
            st.caption(
                f"Mois en cours : {euros(estim['mois_deja_consomme'])} déjà consommés, "
                f"estimation fin de mois **{euros(estim['mois_projection'])}** "
                f"(sur la base des {estim['jours_reference']} derniers jours)."
            )

        jours = bilan.melt(id_vars="jour", value_vars=["kwh_hc", "kwh_hp"], var_name="periode", value_name="kWh")
        jours["periode"] = jours["periode"].map({"kwh_hc": "HC", "kwh_hp": "HP"})
        fig = px.bar(jours, x="jour", y="kWh", color="periode", color_discrete_map=COULEURS,
                     title="Évolution jour par jour")
        moyenne = bilan["kwh_total"].mean()
        fig.add_hline(y=moyenne, line_dash="dot", line_color="gray", annotation_text=f"moyenne {moyenne:.1f} kWh")
        fig.update_layout(legend_title_text="", xaxis_title="")
        st.plotly_chart(fig, width="stretch")

        if len(bilan) >= 14:
            semaines = bilan.assign(semaine=pd.to_datetime(bilan["jour"]).dt.to_period("W").dt.start_time)
            semaines = semaines.groupby("semaine", as_index=False)[["kwh_total", "cout_energie"]].sum()
            fig = px.line(semaines, x="semaine", y="kwh_total", markers=True, title="Évolution semaine par semaine")
            fig.update_layout(xaxis_title="", yaxis_title="kWh")
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

# --- Ma journée : courbe détaillée et annotations ------------------------------

with onglets[1]:
    jours_dispo = sorted(energie["debut"].dt.date.unique()) if energie is not None else []
    jour = st.date_input("Jour", jours_dispo[-1] if jours_dispo else aujourd_hui,
                         min_value=jours_dispo[0] if jours_dispo else None,
                         max_value=aujourd_hui, format="DD/MM/YYYY")
    debut_jour = pd.Timestamp(jour)
    fin_jour = debut_jour + pd.Timedelta(days=1)
    journal_jour = jr.du_jour(journal_df, jour).copy()
    journal_jour["fin_eff"] = jr.fins_effectives(journal_jour, inventaire)
    e_jour = energie[(energie["debut"] >= debut_jour) & (energie["debut"] < fin_jour)] if energie is not None else None

    if e_jour is None or e_jour.empty:
        st.info("Pas de courbe pour ce jour : les annotations restent possibles, elles serviront à "
                "l'apprentissage dès que la courbe Enedis sera disponible.")
    else:
        b = bilan[pd.to_datetime(bilan["jour"]).dt.date == jour]
        if not b.empty:
            b = b.iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("Consommation", f"{b['kwh_total']:.1f} kWh",
                      f"{b['kwh_total'] - bilan['kwh_total'].mean():+.1f} kWh vs moyenne", delta_color="inverse")
            c2.metric("Part en heures creuses", pourcent(b["part_hc"]))
            c3.metric("Coût", euros(b["cout_energie"] + b["abonnement_jour"]))

        milieux = e_jour["debut"] + (e_jour["fin"] - e_jour["debut"]) / 2
        periodes = ["HC" if est_heure_creuse(m.time(), plages) else "HP" for m in milieux]
        fig = go.Figure()
        for per in ("HC", "HP"):
            masque = [p == per for p in periodes]
            fig.add_bar(x=milieux[masque], y=e_jour["puissance_w"][masque], name=per, marker_color=COULEURS[per],
                        width=30 * 60 * 1000 * 0.9)
        for u in journal_jour.itertuples():
            fig.add_vrect(x0=u.debut, x1=u.fin_eff, fillcolor="#264653", opacity=0.15, line_width=0,
                          annotation_text=u.appareil, annotation_position="top left")
        fig.update_layout(title="Puissance moyenne par demi-heure", yaxis_title="W", xaxis_title="",
                          barmode="overlay", legend_title_text="", xaxis_range=[debut_jour, fin_jour])
        st.plotly_chart(fig, width="stretch")
        st.caption("Zones grisées : appareils annotés. Une demi-heure à cheval sur un changement HP/HC prend "
                   "la couleur de la période majoritaire.")

    st.subheader("Qu'est-ce qui a fonctionné ?")
    if lancables.empty:
        st.info("Ajoutez vos appareils dans l'onglet **🔌 Mes appareils** pour pouvoir annoter.")
    else:
        with st.form("annotation", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            nom_app = c1.selectbox("Appareil", lancables["nom"])
            h_debut = c2.time_input("De", value=datetime.strptime("12:00", "%H:%M").time(), step=300)
            h_fin = c3.time_input("À", value=datetime.strptime("13:00", "%H:%M").time(), step=300)
            if st.form_submit_button("Ajouter"):
                d = datetime.combine(jour, h_debut)
                f = datetime.combine(jour, h_fin)
                if f <= d:  # passage de minuit
                    f += timedelta(days=1)
                store.ajouter_utilisation(jr.nouvelle(nom_app, d, f, source="courbe"))
                st.rerun()

        if e_jour is not None and not e_jour.empty and source != SOURCE_DEMO:
            sugg = apprentissage.suggestions(e_jour, journal_df, inventaire, appris,
                                             seuil_w=reglages.get("seuil_suggestion_w", 400), plages=plages)
            if not sugg.empty:
                st.markdown("**Consommations non expliquées** — aidez l'application à apprendre :")
                for i, s_ in sugg.iterrows():
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.write(f"{s_['debut']:%H:%M} → {s_['fin']:%H:%M} · {s_['kwh']:.2f} kWh "
                             f"(≈ {s_['puissance_w']:.0f} W)")
                    options = list(lancables["nom"])
                    choisi = c2.selectbox("C'était", options,
                                          index=options.index(s_["appareil"]) if s_["appareil"] in options else None,
                                          placeholder="— choisir —", key=f"sugg_{i}",
                                          help=f"Proposé d'après {s_['d_apres']}" if s_["appareil"] else None,
                                          label_visibility="collapsed")
                    if c3.button("✔ Confirmer", key=f"ok_{i}", width="stretch", disabled=choisi is None):
                        store.ajouter_utilisation(jr.nouvelle(choisi, s_["debut"].to_pydatetime(),
                                                              s_["fin"].to_pydatetime(), source="suggestion"))
                        st.rerun()

    if not journal_jour.empty:
        st.markdown("**Appareils notés ce jour-là**")
        for u in journal_jour.itertuples():
            c1, c2 = st.columns([4, 1])
            fin_txt = f"{u.fin_eff:%H:%M}" + (" (en cours)" if pd.isna(u.fin) else "")
            c1.write(f"{u.appareil} · {u.debut:%H:%M} → {fin_txt} · _{jr.SOURCES.get(u.source, u.source)}_")
            if c2.button("🗑", key=f"del_{u.id}", help="Supprimer"):
                store.supprimer_utilisation(u.id)
                st.rerun()

    if e_jour is not None and not e_jour.empty and not inventaire.empty and source != SOURCE_DEMO:
        rep = apprentissage.repartition(e_jour, obs[pd.to_datetime(obs["debut"]).dt.date == jour]
                                        if not obs.empty else obs, inventaire)
        if not rep.empty:
            fig = px.bar(rep, x="kwh", y="poste", orientation="h", color="categorie",
                         title="Répartition estimée de la journée")
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, xaxis_title="kWh", yaxis_title="",
                              legend_title_text="")
            st.plotly_chart(fig, width="stretch")

# --- Mes appareils --------------------------------------------------------------

with onglets[2]:
    if mes_appareils is None:
        st.subheader("Quels appareils avez-vous ?")
        st.info("Cochez ceux de votre logement : des valeurs typiques sont pré-remplies. Vous pourrez ensuite "
                "ajuster chaque appareil (puissance, durée, fréquence) et en ajouter.")
        catalogue = appareils.CATALOGUE
        coches = []
        for cat, groupe in catalogue.groupby("categorie", sort=False):
            st.markdown(f"**{cat}**")
            cols = st.columns(3)
            for k, nom in enumerate(groupe["nom"]):
                if cols[k % 3].checkbox(nom, value=True, key=f"cat_{nom}"):
                    coches.append(nom)
        if st.button("Créer ma liste", type="primary", disabled=not coches):
            store.sauvegarder_appareils(catalogue[catalogue["nom"].isin(coches)])
            st.rerun()
    else:
        st.subheader("Mes appareils")
        st.caption(
            "**Usage** : *cycle* = programme à consommation connue (lave-linge, lave-vaisselle) ; "
            "*duree* = puissance × durée (plaques, radiateur, éclairage) ; *permanent* = toujours branché "
            "(réfrigérateur, box). Pour un radiateur, la durée est l'équivalent pleine puissance par jour. "
            "**Déplaçable** : peut être programmé en heures creuses."
        )
        liste = st.data_editor(
            mes_appareils, num_rows="dynamic", hide_index=True, width="stretch", key="editeur_appareils",
            column_config={
                "nom": st.column_config.TextColumn("Appareil", required=True),
                "categorie": st.column_config.SelectboxColumn("Catégorie", options=appareils.CATEGORIES,
                                                              default="Autre"),
                "usage": st.column_config.SelectboxColumn("Usage", options=list(appareils.USAGES), default="duree"),
                "puissance_w": st.column_config.NumberColumn("Puissance (W)", min_value=0, format="%.0f"),
                "duree_h": st.column_config.NumberColumn("Durée (h)", min_value=0.0, format="%.2f"),
                "kwh_cycle": st.column_config.NumberColumn("kWh / cycle", min_value=0.0, format="%.2f"),
                "utilisations_semaine": st.column_config.NumberColumn("Fois / semaine", min_value=0.0,
                                                                      format="%.1f"),
                "mois_par_an": st.column_config.NumberColumn("Mois / an", min_value=0.0, max_value=12.0,
                                                             format="%.0f"),
                "quantite": st.column_config.NumberColumn("Nombre", min_value=1, step=1),
                "deplacable": st.column_config.CheckboxColumn("Déplaçable"),
            },
        )
        c1, c2 = st.columns([1, 2])
        if c1.button("Enregistrer mes appareils", type="primary"):
            try:
                store.sauvegarder_appareils(liste)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Appareils enregistrés.")
                st.rerun()
        absents = [n for n in appareils.CATALOGUE["nom"] if n not in set(mes_appareils["nom"])]
        if absents:
            with c2.popover("➕ Ajouter depuis le catalogue"):
                ajout = st.multiselect("Appareils", absents)
                if st.button("Ajouter", disabled=not ajout):
                    cat = appareils.CATALOGUE
                    store.sauvegarder_appareils(pd.concat([mes_appareils, cat[cat["nom"].isin(ajout)]]))
                    st.rerun()
        st.caption("Renommer un appareil détache les utilisations déjà notées sous l'ancien nom.")

        st.subheader("Consommation théorique par an")
        inv_bilan = appareils.bilan_inventaire(mes_appareils)
        total_theorique = inv_bilan["kwh_an"].sum()
        par_cat = inv_bilan.groupby("categorie", as_index=False)["kwh_an"].sum().sort_values("kwh_an")
        fig = px.bar(par_cat, x="kwh_an", y="categorie", orientation="h",
                     text=par_cat["kwh_an"].map(lambda v: f"{v:.0f} kWh"))
        fig.update_layout(xaxis_title="kWh / an", yaxis_title="")
        st.plotly_chart(fig, width="stretch")
        texte = f"D'après vos réglages : **{total_theorique:.0f} kWh/an**."
        if not bilan.empty:
            mesure = bilan["kwh_total"].mean() * 365
            texte += (f" Votre consommation mesurée projetée sur un an : **{mesure:.0f} kWh**. "
                      "Un grand écart indique des réglages à revoir (attention, la saison compte : "
                      "le chauffage pèse surtout en hiver).")
        st.markdown(texte)

        st.subheader("Ce que l'application a appris")
        if appris.empty:
            st.info("Rien encore. Notez quand vos appareils fonctionnent (bouton « ▶ Je lance » ou onglet "
                    "**📅 Ma journée**) : dès que la courbe Enedis couvre ces moments, l'application mesure leur "
                    "consommation réelle.")
        else:
            st.dataframe(
                appris.assign(
                    kwh_appris=appris["kwh_appris"].map(lambda v: f"{v:.2f}"),
                    kwh_declare=appris["kwh_declare"].map(lambda v: "–" if pd.isna(v) else f"{v:.2f}"),
                    puissance_apprise_w=appris["puissance_apprise_w"].map(lambda v: f"{v:.0f}"),
                )[["appareil", "n", "kwh_appris", "kwh_declare", "puissance_apprise_w", "fiabilite"]].rename(columns={
                    "appareil": "Appareil", "n": "Mesures", "kwh_appris": "kWh mesurés / utilisation",
                    "kwh_declare": "kWh d'après vos réglages", "puissance_apprise_w": "Puissance mesurée (W)",
                    "fiabilite": "Fiabilité"}),
                hide_index=True, width="stretch",
            )
            st.caption("Mesure au-dessus de la consommation de fond de la journée, à partir de la courbe à 30 min : "
                       "la fiabilité augmente avec le nombre d'utilisations notées.")

        with st.expander(f"Journal complet ({len(journal_df)} utilisations)"):
            if journal_df.empty:
                st.write("Aucune utilisation notée.")
            else:
                vue = journal_df.sort_values("debut", ascending=False).head(200)
                st.dataframe(
                    vue.assign(debut=vue["debut"].dt.strftime("%d/%m %H:%M"),
                               fin=vue["fin"].dt.strftime("%d/%m %H:%M").fillna("en cours"),
                               source=vue["source"].map(lambda s_: jr.SOURCES.get(s_, s_)))
                    [["appareil", "debut", "fin", "source"]].rename(columns={
                        "appareil": "Appareil", "debut": "Début", "fin": "Fin", "source": "Origine"}),
                    hide_index=True, width="stretch",
                )

        st.subheader("Rappels dans mon agenda")
        minutes = st.slider("Me prévenir … minutes avant le début des heures creuses", 0, 60, 10, step=5)
        st.download_button(
            "📅 Télécharger les rappels (.ics)",
            appareils.calendrier_rappels(plages, minutes),
            file_name="rappels_heures_creuses.ics", mime="text/calendar",
        )
        st.caption("Ouvrez ce fichier sur votre téléphone ou importez-le dans Google Agenda / Apple Calendrier / "
                   "Outlook : vous recevrez une notification chaque jour. À refaire si vos plages HC changent.")

# --- Optimisation -------------------------------------------------------------

with onglets[3]:
    st.subheader("Mes pistes d'économies")
    liste_conseils = [] if mes_appareils is None else optimisation.conseils(
        inventaire, journal_df, obs, appris, plages, grille, talon)
    if not liste_conseils:
        st.info("Renseignez vos appareils (onglet **🔌 Mes appareils**) pour recevoir des conseils personnalisés.")
    else:
        total_gain = sum(c["gain_an"] for c in liste_conseils)
        st.metric("Économie potentielle", f"{euros(total_gain)} / an",
                  "si vous appliquez tous ces conseils", delta_color="off")
        for c in liste_conseils:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{c['titre']}**")
                if c["gain_an"] > 0:
                    c2.markdown(f"**{euros(c['gain_an'])}**/an")
                st.caption(f"{c['detail']} · Fiabilité : {c['fiabilite']}")
        st.caption("Fiabilité *mesurée* : d'après vos utilisations notées et la courbe ; *estimée* : d'après vos "
                   "utilisations et vos réglages ; *théorique* : d'après vos réglages seuls ; *ordre de grandeur* : "
                   "moyennes nationales (ADEME).")

    if not bilan.empty:
        ecart = grille.prix_hp - grille.prix_hc
        if classe is not None:
            with st.expander("Consommation de fond, journée type et pics en heures pleines"):
                prix_moyen = (bilan["cout_energie"].sum() / bilan["kwh_total"].sum()) if bilan["kwh_total"].sum() \
                    else grille.prix_hp
                c1, c2 = st.columns(2)
                c1.metric("Consommation de fond (talon)", f"{talon:.0f} W")
                c2.metric("Coût annuel du talon", euros(calculs.cout_annuel_talon(talon, prix_moyen)))

                profil = calculs.profil_horaire(classe)
                fig = px.bar(profil, x="heure", y="puissance_w", color="periode", color_discrete_map=COULEURS,
                             title="Journée type")
                fig.update_layout(xaxis_title="", yaxis_title="Puissance moyenne (W)", legend_title_text="")
                st.plotly_chart(fig, width="stretch")

                seuil = st.number_input("Seuil « grosse consommation » (W)", 300, 6000,
                                        int(reglages["seuil_pic_w"]), step=100)
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

        st.subheader("Quelle offre EDF pour moi ?")
        offres = offres_depuis_grille(grille, plages)
        if energie is not None:
            resultats = [calculs.cout_offre(energie, o) for o in offres]
            res_tempo = calculs.cout_tempo(energie, tempo)
            resultats.append(res_tempo)
        else:
            res_tempo = None
            resultats = calculs.comparer_offres_kwh(bilan["kwh_hp"].sum(), bilan["kwh_hc"].sum(), len(bilan), offres)
            st.caption("Tempo ne peut être simulé qu'avec la courbe de charge (Enedis, fichier exporté ou démo).")
        res = pd.DataFrame(resultats).sort_values("total_annuel")
        fig = px.bar(res, x="total_annuel", y="offre", orientation="h", text=res["total_annuel"].map(euros))
        fig.update_layout(xaxis_title="Coût annuel estimé (€)", yaxis_title="",
                          yaxis={"categoryorder": "total descending"})
        st.plotly_chart(fig, width="stretch")
        meilleure = res.iloc[0]
        actuelle = res[res["offre"].str.startswith("Heures creuses")].iloc[0]
        if meilleure["offre"] != actuelle["offre"]:
            st.success(f"**{meilleure['offre']}** serait la moins chère : environ "
                       f"{euros(actuelle['total_annuel'] - meilleure['total_annuel'])} d'économie par an "
                       "(estimation extrapolée sur la période affichée).")
        else:
            st.success("Votre option actuelle (Heures creuses) est la moins chère sur la période affichée.")
        if res_tempo is not None:
            couleurs = res_tempo["jours_couleurs"]
            st.caption(
                f"Tempo : heures creuses fixes 22h-6h. Couleurs supposées sur la période : "
                f"{couleurs.get('bleu', 0)} bleus, {couleurs.get('blanc', 0)} blancs, {couleurs.get('rouge', 0)} rouges "
                "(vos jours les plus consommateurs en hiver sont supposés blancs/rouges). Avec un chauffage électrique, "
                "les jours rouges (prix HP très élevé) pèsent lourd : à simuler sur une période d'hiver."
            )
        st.caption("Projection annuelle à partir de la période affichée : choisissez une période longue, et gardez "
                   "en tête que l'hiver (chauffage) coûte plus que l'été.")

        kwh_an = bilan["kwh_total"].mean() * 365
        seuil_hc = calculs.part_hc_rentable(grille.abonnement_hc_mensuel, grille.prix_hp, grille.prix_hc,
                                            grille.abonnement_base_mensuel, grille.prix_base, kwh_an)
        part_actuelle = bilan["kwh_hc"].sum() / bilan["kwh_total"].sum()
        if seuil_hc is None:
            st.warning("Avec ces prix, l'option Heures creuses n'est jamais plus avantageuse que Base.")
        else:
            st.info(f"Pour ~{kwh_an:.0f} kWh/an, l'option HC bat l'option Base au-delà de **{seuil_hc:.0%}** "
                    f"de consommation en heures creuses. Vous êtes à **{part_actuelle:.0%}**.")

# --- Relevés -----------------------------------------------------------------

with onglets[4]:
    st.subheader("Saisir un relevé du compteur")
    st.caption("Sur le Linky, appuyez sur « + » jusqu'aux index HEURES CREUSES et HEURES PLEINES.")
    with st.form("releve", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        jour_releve = c1.date_input("Date", aujourd_hui, max_value=aujourd_hui)
        heure = c2.time_input("Heure", jr.maintenant().time())
        index_hc = c3.number_input("Index HC (kWh)", min_value=0.0, step=1.0, format="%.0f")
        index_hp = c4.number_input("Index HP (kWh)", min_value=0.0, step=1.0, format="%.0f")
        if st.form_submit_button("Enregistrer"):
            moment = datetime.combine(jour_releve, heure)
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

# --- Réglages -----------------------------------------------------------------

with onglets[5]:
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

    st.subheader("Tarifs Tempo (pour la comparaison)")
    st.caption("Prix TTC indicatifs pour 6 kVA : vérifiez-les sur edf.fr.")
    with st.form("tempo"):
        c1, c2, c3 = st.columns(3)
        valeurs = {"abonnement_mensuel": c1.number_input("Abonnement (€/mois)", 0.0, 100.0,
                                                         tempo.abonnement_mensuel, format="%.2f")}
        for col, couleur in zip((c1, c2, c3), ("bleu", "blanc", "rouge")):
            for per in ("hc", "hp"):
                cle = f"{couleur}_{per}"
                valeurs[cle] = col.number_input(f"{couleur.capitalize()} {per.upper()} (€/kWh)", 0.0, 2.0,
                                                getattr(tempo, cle), step=0.001, format="%.4f")
        if st.form_submit_button("Enregistrer les tarifs Tempo"):
            store.sauvegarder_reglages({**reglages, "tempo": {**vars(tempo), **valeurs}})
            st.success("Tarifs Tempo enregistrés.")
            st.rerun()
