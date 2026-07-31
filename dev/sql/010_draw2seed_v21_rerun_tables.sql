-- Derived Draw2Seed v2.1 retrospective results.
-- Original v2 enrollment and verification rows are never overwritten.

create extension if not exists pgcrypto;

create table if not exists public.draw2seed_v21_enrollment_reruns (
  id uuid primary key default gen_random_uuid(),
  source_enrollment_id uuid not null references public.draw2seed_v2_enrollments(id)
    on update cascade on delete restrict,
  participant_id text,
  source_algorithm_version text,
  rerun_algorithm_version text not null,
  rerun_algorithm_code_hash text not null,
  rerun_config_version text not null,
  attempt_count integer not null,
  accepted_for_demo boolean,
  stability_score double precision,
  recommended_profile text,
  verification_reference_count integer,
  seed_quality_score double precision,
  complexity_class text,
  regenerated_analysis_result jsonb not null,
  generated_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (
    source_enrollment_id,
    rerun_algorithm_code_hash,
    rerun_config_version
  )
);

create table if not exists public.draw2seed_v21_verification_reruns (
  id uuid primary key default gen_random_uuid(),
  source_verification_id uuid not null references public.draw2seed_v2_verifications(id)
    on update cascade on delete restrict,
  source_enrollment_id uuid not null references public.draw2seed_v2_enrollments(id)
    on update cascade on delete restrict,
  attempt_type text not null,
  source_algorithm_version text,
  rerun_algorithm_version text not null,
  rerun_algorithm_code_hash text not null,
  rerun_config_version text not null,
  original_accepted boolean not null,
  rerun_accepted boolean not null,
  decision_transition text not null check (
    decision_transition in (
      'accept_to_accept', 'accept_to_reject',
      'reject_to_accept', 'reject_to_reject'
    )
  ),
  profile text,
  final_score double precision,
  token_score double precision,
  token_score_weighted double precision,
  geometry_final double precision,
  layout_score double precision,
  relation_score double precision,
  topology_score double precision,
  curve_score double precision,
  stroke_shape_score double precision,
  selected_reference_attempt integer,
  verification_reference_count integer,
  second_reference_support double precision,
  single_open_dtw_score double precision,
  single_open_recovery_pass boolean,
  failure_reasons jsonb,
  overridden_failure_reasons jsonb,
  rerun_result jsonb not null,
  generated_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (
    source_verification_id,
    rerun_algorithm_code_hash,
    rerun_config_version
  )
);

create index if not exists draw2seed_v21_enrollment_source_idx
  on public.draw2seed_v21_enrollment_reruns(source_enrollment_id);

create index if not exists draw2seed_v21_verification_source_idx
  on public.draw2seed_v21_verification_reruns(source_verification_id);

create index if not exists draw2seed_v21_verification_attempt_type_idx
  on public.draw2seed_v21_verification_reruns(attempt_type, rerun_accepted);

alter table public.draw2seed_v21_enrollment_reruns enable row level security;
alter table public.draw2seed_v21_verification_reruns enable row level security;

-- No anon/authenticated policies are created. Only the local service-role
-- rerun tool and trusted development environment should access these tables.

revoke all on public.draw2seed_v21_enrollment_reruns from anon, authenticated;
revoke all on public.draw2seed_v21_verification_reruns from anon, authenticated;

notify pgrst, 'reload schema';
