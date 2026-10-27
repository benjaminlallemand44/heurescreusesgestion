-- Données de l'application heures creuses (usage personnel, un seul utilisateur).
-- RLS activé sans politique : seules les clés secrètes (côté serveur Streamlit) y accèdent.

create table public.releves (
  date timestamp primary key,
  index_hc double precision not null check (index_hc >= 0),
  index_hp double precision not null check (index_hp >= 0)
);

create table public.tarifs (
  date_debut date primary key,
  abonnement_hc_mensuel double precision not null,
  prix_hp double precision not null,
  prix_hc double precision not null,
  abonnement_base_mensuel double precision not null,
  prix_base double precision not null
);

create table public.appareils (
  nom text primary key,
  kwh_cycle double precision not null,
  duree_h double precision not null,
  cycles_semaine double precision not null
);

create table public.reglages (
  id smallint primary key default 1 check (id = 1),
  valeurs jsonb not null default '{}'::jsonb
);

alter table public.releves enable row level security;
alter table public.tarifs enable row level security;
alter table public.appareils enable row level security;
alter table public.reglages enable row level security;

-- Aucun accès via les clés publiques
revoke all on public.releves, public.tarifs, public.appareils, public.reglages from anon, authenticated;
