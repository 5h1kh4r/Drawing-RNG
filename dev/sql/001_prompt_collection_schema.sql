create table if not exists public.stroke_samples (
  id uuid primary key default gen_random_uuid(),
  participant_id text,
  concept text,
  redraw_id integer,
  sample_name text,
  notes text,
  strokes jsonb not null,
  params jsonb,
  canvas_size jsonb,
  serialized text,
  ui_version text,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists stroke_samples_concept_idx on public.stroke_samples (concept);
create index if not exists stroke_samples_participant_idx on public.stroke_samples (participant_id);

-- If your Flask backend uses the service_role key, no public insert policy is needed.
-- Keep service_role only on the server, never in browser JS.
