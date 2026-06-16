-- Verification attempt logging for Drawing-RNG evaluation datasets.
-- Run this in Supabase SQL Editor after 002_seed_enrollment_schema.sql.

create table if not exists public.drawing_seed_verifications (
  id uuid primary key default gen_random_uuid(),

  enrollment_id uuid,
  participant_id text,
  seed_label text,
  attempt_type text,

  redraw_strokes jsonb not null,
  verification_result jsonb not null,

  accepted boolean,
  profile text,
  final_score double precision,
  token_score double precision,
  geometry_final double precision,
  layout_score double precision,
  relation_score double precision,
  curve_score double precision,
  stroke_shape_score double precision,
  fuzzy_ok boolean,
  fuzzy_mode text,
  failure_reasons jsonb,

  ui_version text,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists drawing_seed_verifications_enrollment_idx
on public.drawing_seed_verifications (enrollment_id);

create index if not exists drawing_seed_verifications_attempt_type_idx
on public.drawing_seed_verifications (attempt_type);

create index if not exists drawing_seed_verifications_scores_idx
on public.drawing_seed_verifications (accepted, final_score);
