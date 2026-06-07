create table if not exists public.drawing_seed_enrollments (
  id uuid primary key default gen_random_uuid(),
  participant_id text,
  seed_label text,
  attempt_count integer,
  attempts jsonb not null,
  analysis_result jsonb not null,
  accepted_for_demo boolean,
  stability_score double precision,
  recommended_profile text,
  public_salt text,
  ui_version text,
  notes text,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists drawing_seed_enrollments_participant_idx
on public.drawing_seed_enrollments (participant_id);

create index if not exists drawing_seed_enrollments_score_idx
on public.drawing_seed_enrollments (stability_score);
