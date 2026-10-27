-- Inventaire détaillé des appareils et journal d'utilisation.

alter table public.appareils rename column cycles_semaine to utilisations_semaine;
alter table public.appareils
  add column categorie text not null default 'Autre',
  add column usage text not null default 'cycle' check (usage in ('cycle', 'duree', 'permanent')),
  add column puissance_w double precision not null default 0,
  add column mois_par_an double precision not null default 12,
  add column quantite integer not null default 1,
  add column deplacable boolean not null default false;

create table public.utilisations (
  id text primary key,
  appareil text not null,
  debut timestamp not null,
  fin timestamp,
  source text not null default 'bouton',
  note text not null default '',
  check (fin is null or fin > debut)
);
create index utilisations_debut_idx on public.utilisations (debut);

alter table public.utilisations enable row level security;
revoke all on public.utilisations from anon, authenticated;
