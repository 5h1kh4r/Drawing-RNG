-- Phase 3.1: Seed Quality Score diagnostics.
-- Safe to run repeatedly.

alter table public.drawing_seed_enrollments
add column if not exists seed_quality_score double precision,
add column if not exists seed_quality_label text,
add column if not exists seed_quality_hard_reject boolean,
add column if not exists complexity_class text,
add column if not exists scene_stability_score double precision;

create index if not exists drawing_seed_enrollments_quality_idx
on public.drawing_seed_enrollments (seed_quality_score);

create index if not exists drawing_seed_enrollments_complexity_idx
on public.drawing_seed_enrollments (complexity_class);

-- Refresh PostgREST/Supabase schema cache after migration.
select pg_notify('pgrst', 'reload schema');
