-- Draw2Seed v2 clean research dataset.
-- Run once in the Supabase SQL Editor before deploying the patched app.
-- Old tables are intentionally left untouched.

create extension if not exists pgcrypto;

create table if not exists public.draw2seed_v2_participants (
  participant_id text primary key,
  participant_role text not null default 'participant'
    check (participant_role in ('participant', 'researcher')),
  consent_version text not null default 'v2-consent-2026-07',
  dataset_version text not null default 'draw2seed_v2_clean',
  collection_site text,
  collection_session text,
  notes text,
  created_at timestamptz not null default now()
);

create table if not exists public.draw2seed_v2_enrollments (
  id uuid primary key default gen_random_uuid(),
  participant_id text references public.draw2seed_v2_participants(participant_id)
    on update cascade on delete restrict,
  dataset_version text not null default 'draw2seed_v2_clean',
  algorithm_version text not null,
  algorithm_code_hash text not null,
  config_version text not null,
  collection_site text,
  collection_session text,
  seed_label text,
  attempt_count integer not null check (attempt_count >= 3),
  attempts jsonb not null,
  analysis_result jsonb not null,
  accepted_for_demo boolean,
  stability_score double precision,
  recommended_profile text,
  seed_quality_score double precision,
  seed_quality_label text,
  seed_quality_hard_reject boolean,
  complexity_class text,
  scene_stability_score double precision,
  timing_stability_score double precision,
  public_salt text,
  algorithm_history jsonb not null default '[]'::jsonb,
  device_type text,
  input_method text,
  ui_version text,
  notes text,
  user_agent text,
  created_at timestamptz not null default now()
);

create table if not exists public.draw2seed_v2_verifications (
  id uuid primary key default gen_random_uuid(),
  enrollment_id uuid not null references public.draw2seed_v2_enrollments(id)
    on update cascade on delete restrict,
  actor_participant_id text references public.draw2seed_v2_participants(participant_id)
    on update cascade on delete restrict,
  owner_participant_id text references public.draw2seed_v2_participants(participant_id)
    on update cascade on delete restrict,
  participant_id text,
  dataset_version text not null default 'draw2seed_v2_clean',
  algorithm_version text not null,
  algorithm_code_hash text not null,
  config_version text not null,
  collection_site text,
  collection_session text,
  seed_label text,
  attempt_type text not null check (
    attempt_type in (
      'owner_test','blind_impostor','informed_forgery','near_miss',
      'true_wrong_shape','bad_sample','step_up_component'
    )
  ),
  redraw_strokes jsonb not null,
  verification_result jsonb not null,
  gate_trace jsonb,
  accepted boolean,
  primary_accepted boolean,
  profile text,
  final_score double precision,
  token_score double precision,
  token_score_weighted double precision,
  token_bigram_score double precision,
  geometry_final double precision,
  component_count_score double precision,
  layout_score double precision,
  relation_score double precision,
  topology_score double precision,
  curve_score double precision,
  stroke_shape_score double precision,
  closed_style_score double precision,
  structural_score double precision,
  structural_gate_pass boolean,
  structural_failure_reasons jsonb,
  shape_lock_baseline_score double precision,
  shape_lock_baseline_threshold double precision,
  shape_lock_baseline_pass boolean,
  algorithm_history jsonb not null default '[]'::jsonb,
  complex_scene_mode boolean,
  scene_final double precision,
  scene_assignment double precision,
  scene_raster double precision,
  scene_relation double precision,
  timing_final double precision,
  step_up_required boolean,
  step_up_passed boolean,
  component_score double precision,
  fuzzy_ok boolean,
  fuzzy_mode text,
  fuzzy_hamming_distance integer,
  fuzzy_max_correctable_bits integer,
  failure_reasons jsonb,
  geometry_failure_reasons jsonb,
  scene_failure_reasons jsonb,
  device_type text,
  input_method text,
  observation_mode text not null default 'unknown'
    check (observation_mode in ('none','static_preview','live_observation','video','unknown')),
  practice_allowed boolean,
  practice_attempts integer check (practice_attempts is null or practice_attempts >= 0),
  ui_version text,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists draw2seed_v2_enrollments_participant_idx on public.draw2seed_v2_enrollments(participant_id);
create index if not exists draw2seed_v2_enrollments_quality_idx on public.draw2seed_v2_enrollments(seed_quality_score, stability_score);
create index if not exists draw2seed_v2_verifications_enrollment_idx on public.draw2seed_v2_verifications(enrollment_id);
create index if not exists draw2seed_v2_verifications_actor_idx on public.draw2seed_v2_verifications(actor_participant_id);
create index if not exists draw2seed_v2_verifications_type_idx on public.draw2seed_v2_verifications(attempt_type);
create index if not exists draw2seed_v2_verifications_decision_idx on public.draw2seed_v2_verifications(accepted, final_score);

alter table public.draw2seed_v2_participants enable row level security;
alter table public.draw2seed_v2_enrollments enable row level security;
alter table public.draw2seed_v2_verifications enable row level security;

revoke all on table public.draw2seed_v2_participants from anon, authenticated;
revoke all on table public.draw2seed_v2_enrollments from anon, authenticated;
revoke all on table public.draw2seed_v2_verifications from anon, authenticated;

grant insert on table public.draw2seed_v2_participants to anon;
grant insert on table public.draw2seed_v2_enrollments to anon;
grant insert on table public.draw2seed_v2_verifications to anon;

drop policy if exists "draw2seed_v2_public_insert_participants" on public.draw2seed_v2_participants;
create policy "draw2seed_v2_public_insert_participants" on public.draw2seed_v2_participants
  for insert to anon with check (dataset_version = 'draw2seed_v2_clean');

drop policy if exists "draw2seed_v2_public_insert_enrollments" on public.draw2seed_v2_enrollments;
create policy "draw2seed_v2_public_insert_enrollments" on public.draw2seed_v2_enrollments
  for insert to anon with check (dataset_version = 'draw2seed_v2_clean' and attempt_count >= 3);

drop policy if exists "draw2seed_v2_public_insert_verifications" on public.draw2seed_v2_verifications;
create policy "draw2seed_v2_public_insert_verifications" on public.draw2seed_v2_verifications
  for insert to anon with check (dataset_version = 'draw2seed_v2_clean' and enrollment_id is not null);
