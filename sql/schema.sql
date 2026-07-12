-- Schema do Flow (rodar no Supabase → SQL Editor).
-- Em desenvolvimento a API pode criar as tabelas sozinha (DEV_CREATE_TABLES=true),
-- mas em produção rode este arquivo.

create extension if not exists "pgcrypto";  -- gen_random_uuid()

create table if not exists public.carteiras (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  nome        varchar(120) not null,
  created_at  timestamptz not null default now()
);
create index if not exists carteiras_user_id_idx on public.carteiras (user_id);

create table if not exists public.transacoes (
  id            uuid primary key default gen_random_uuid(),
  carteira_id   uuid not null references public.carteiras (id) on delete cascade,
  ticker        varchar(20) not null,
  nome          varchar(120),
  tipo_ativo    varchar(40),
  operacao      varchar(10) not null check (operacao in ('compra', 'venda')),
  quantidade    numeric(20, 8) not null,
  preco_unit    numeric(20, 4) not null,
  outros_custos numeric(20, 4) not null default 0,
  data          date not null,
  fonte         varchar(40) not null default 'Manual',
  created_at    timestamptz not null default now()
);
create index if not exists transacoes_carteira_id_idx on public.transacoes (carteira_id);

create table if not exists public.proventos (
  id             uuid primary key default gen_random_uuid(),
  carteira_id    uuid not null references public.carteiras (id) on delete cascade,
  ticker         varchar(20) not null,
  tipo_provento  varchar(40) not null,
  data_com       date,
  data_pagamento date,
  valor_por_acao numeric(20, 6) not null,
  -- Calculados na Data COM via motor de posição (podem ser nulos).
  quantidade     numeric(20, 8),
  pm_historico   numeric(20, 4),
  valor_recebido numeric(20, 2),
  yoc_evento     numeric(20, 4),
  created_at     timestamptz not null default now()
);
create index if not exists proventos_carteira_id_idx on public.proventos (carteira_id);
