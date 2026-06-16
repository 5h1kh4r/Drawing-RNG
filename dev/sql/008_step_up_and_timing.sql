-- Phase 3.2: step-up component challenge + timing/rhythm diagnostics

alter table public.drawing_seed_enrollments
add column if not exists timing_stability_score double precision;

alter table public.drawing_seed_verifications
add column if not exists timing_final double precision,
add column if not exists step_up_required boolean,
add column if not exists step_up_passed boolean,
add column if not exists component_score double precision;

select pg_notify('pgrst', 'reload schema');
