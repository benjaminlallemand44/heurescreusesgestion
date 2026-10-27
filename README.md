# ⚡ Heures creuses – gestion de ma consommation

Application Streamlit pour suivre et optimiser sa consommation d'électricité avec un contrat
**tarif bleu, option heures creuses** (ex. 6 kVA) et un compteur Linky.

## 📊 Fonctionnalités

**Suivi**
- Tableau de bord : consommation par jour en heures pleines / heures creuses, part HC, coût abonnement compris
- Estimation de la facture du mois en cours et projection annuelle
- Pic de puissance quotidien comparé à la puissance souscrite
- Plages HC personnalisables, y compris plusieurs plages et des horaires hors demi-heure
  (ex. `00:56-06:56; 14:56-16:56`) : une demi-heure à cheval est répartie au prorata entre HP et HC
- Historique daté des tarifs : chaque changement de prix s'applique à partir de sa date, les coûts passés restent justes

**Optimisation**
- Talon de consommation (ce qui tourne en permanence) et son coût annuel
- Profil de la journée type, coloré HP/HC
- Détection des grosses consommations en heures pleines (ballon d'eau chaude mal programmé, lave-linge lancé en HP…) avec le gain possible
- Simulateur d'offres : heures creuses, Base, et toute offre personnalisée (autre fournisseur, futures plages HC)
- Seuil de rentabilité de l'option HC par rapport à Base
- Mes appareils : l'application demande au premier lancement quels appareils vous avez, puis propose une heure de lancement et calcule le gain par cycle et par an
- Rappels : fichier `.ics` à importer dans votre agenda pour être prévenu avant le début des heures creuses

## 🔌 Sources de données

| Source | Ce qu'il faut |
|---|---|
| Démo | Rien : données fictives pour découvrir l'application |
| Relevés manuels | Relever les index HC et HP du Linky (bouton « + ») et les saisir dans l'onglet Relevés |
| Fichier CSV | Un fichier `date;puissance_w` (courbe de charge, pas de 30 min) |
| Enedis via Conso API | Collecte horaire activée sur l'espace Enedis + token Conso API (voir ci-dessous) |

### Configurer Conso API

1. Sur votre espace client Enedis, activez **l'enregistrement** et **la collecte** de la consommation horaire.
2. Sur [conso.boris.sh](https://conso.boris.sh), donnez votre consentement (redirection vers Enedis) et récupérez votre token.
3. Copiez `.streamlit/secrets.toml.example` en `.streamlit/secrets.toml` et collez-y le token
   (ou définissez la variable d'environnement `CONSO_API_TOKEN`).
4. Renseignez votre PRM (14 chiffres) dans l'onglet Réglages.

⚠️ Le token est un secret : `secrets.toml` est exclu de Git, ne le copiez jamais dans le code.

## 💾 Stockage des données

- **Supabase** (recommandé, indispensable sur Streamlit Cloud) : si `SUPABASE_URL` et `SUPABASE_SECRET_KEY`
  sont configurés, relevés, tarifs, appareils et réglages sont enregistrés dans le projet Supabase
  « heures-creuses » (tables `releves`, `tarifs`, `appareils`, `reglages`).
  Les tables sont protégées : aucune clé publique n'y a accès, seule la clé secrète de l'application.
- **Fichiers locaux** sinon, dans le dossier `data/` (exclu de Git).

La barre latérale indique où les données sont enregistrées.

## ☁️ Mise en ligne (Streamlit Community Cloud)

1. Sur [share.streamlit.io](https://share.streamlit.io), *Create app* → dépôt `benjaminlallemand44/heurescreusesgestion`,
   fichier `app.py`.
2. Dans *Advanced settings → Secrets* (ou après coup : *Settings → Secrets*), collez le contenu de
   `.streamlit/secrets.toml.example` avec votre clé secrète Supabase et votre token Conso API.
3. Dans *Settings → Sharing*, limitez l'accès à votre adresse e-mail.

## 🚀 Installation

```bash
git clone https://github.com/benjaminlallemand44/heurescreusesgestion.git
cd heurescreusesgestion
pip install -r requirements.txt
streamlit run app.py
```

Au premier lancement, allez dans **⚙️ Réglages** pour saisir vos plages d'heures creuses et vérifier les tarifs
(les valeurs par défaut sont indicatives).


## 🧪 Tests

```bash
pytest
```

## 📁 Structure

```
app.py            Interface Streamlit
hc/tarifs.py      Plages HC, grilles tarifaires datées, offres
hc/calculs.py     Répartition HP/HC, coûts, talon, pics, simulateur, estimation de facture
hc/releves.py     Relevés manuels des index
hc/enedis.py      Conso API et import CSV
hc/appareils.py   Catalogue d'appareils, conseils de lancement, rappels .ics
hc/stockage.py    Sauvegarde : Supabase ou fichiers locaux (data/)
hc/demo.py        Courbe de charge fictive
```
