-- Draw2Seed v2.2 migration
-- Run once in the Supabase SQL Editor before deploying/rerunning v2.2.
-- It canonicalizes legacy labels, fixes the attempt_type constraint, adds
-- v2.2 diagnostics/history columns, and creates one-time pre-v2.2 snapshots.

begin;

create table if not exists public.draw2seed_v2_enrollments_backup_pre_v22
as table public.draw2seed_v2_enrollments;

create table if not exists public.draw2seed_v2_verifications_backup_pre_v22
as table public.draw2seed_v2_verifications;

alter table public.draw2seed_v2_enrollments
  add column if not exists algorithm_history jsonb not null default '[]'::jsonb;

alter table public.draw2seed_v2_verifications
  add column if not exists algorithm_history jsonb not null default '[]'::jsonb,
  add column if not exists structural_score double precision,
  add column if not exists structural_gate_pass boolean,
  add column if not exists structural_failure_reasons jsonb,
  add column if not exists shape_lock_baseline_score double precision,
  add column if not exists shape_lock_baseline_threshold double precision,
  add column if not exists shape_lock_baseline_pass boolean;

-- Canonicalize legacy labels before rebuilding the constraint.
update public.draw2seed_v2_verifications
set attempt_type = case attempt_type
  when 'wrong_shape' then 'true_wrong_shape'
  when 'concept_variant' then 'near_miss'
  when 'ambiguous' then 'bad_sample'
  else attempt_type
end
where attempt_type in ('wrong_shape', 'concept_variant', 'ambiguous');

alter table public.draw2seed_v2_verifications
  drop constraint if exists draw2seed_v2_verifications_attempt_type_check;

alter table public.draw2seed_v2_verifications
  add constraint draw2seed_v2_verifications_attempt_type_check
  check (
    attempt_type in (
      'owner_test',
      'blind_impostor',
      'informed_forgery',
      'near_miss',
      'true_wrong_shape',
      'bad_sample',
      'step_up_component'
    )
  );

create index if not exists draw2seed_v2_verifications_structural_idx
  on public.draw2seed_v2_verifications(structural_gate_pass, structural_score);

create index if not exists draw2seed_v2_verifications_baseline_idx
  on public.draw2seed_v2_verifications(shape_lock_baseline_pass, shape_lock_baseline_score);

commit;

notify pgrst, 'reload schema';

-- Verification query:
-- select attempt_type, count(*)
-- from public.draw2seed_v2_verifications
-- group by attempt_type
-- order by attempt_type;
