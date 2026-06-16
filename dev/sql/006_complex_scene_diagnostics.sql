-- Complex-scene verifier diagnostics for Drawing-RNG Phase 2.10.
-- These columns are optional but make Supabase review/metrics queries easier.

alter table public.drawing_seed_verifications
add column if not exists complex_scene_mode boolean,
add column if not exists scene_final double precision,
add column if not exists scene_assignment double precision,
add column if not exists scene_raster double precision,
add column if not exists scene_relation double precision;

create index if not exists drawing_seed_verifications_complex_scene_idx
on public.drawing_seed_verifications (complex_scene_mode);
