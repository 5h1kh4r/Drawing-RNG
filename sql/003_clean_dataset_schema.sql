create table if not exists public.stroke_samples_clean (
  id uuid primary key default gen_random_uuid(),
  source_id uuid,
  clean_participant_id text,
  clean_concept text,
  clean_redraw_id integer,
  original_participant_id text,
  original_concept text,
  original_redraw_id integer,
  original_sample_name text,
  strokes jsonb not null,
  params jsonb,
  tokens jsonb,
  serialized text,
  stats jsonb,
  seed_material_hex text,
  token_profile text,
  tokenize_ok boolean,
  tokenize_error text,
  image_rel_path text,
  row_id_prefix text,
  created_at timestamptz,
  cleaned_at timestamptz not null default now(),
  notes text
);

create index if not exists stroke_samples_clean_source_id_idx on public.stroke_samples_clean (source_id);
create index if not exists stroke_samples_clean_concept_idx on public.stroke_samples_clean (clean_concept);
create index if not exists stroke_samples_clean_participant_idx on public.stroke_samples_clean (clean_participant_id);
