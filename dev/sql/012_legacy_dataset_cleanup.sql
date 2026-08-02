-- Draw2Seed legacy dataset cleanup preparation.
-- Run once in the Supabase SQL Editor before relabelling legacy attempts.
-- It creates immutable pre-cleanup snapshots and permits canonical labels.

begin;

create table if not exists public.drawing_seed_enrollments_backup_pre_cleanup
as table public.drawing_seed_enrollments;

create table if not exists public.drawing_seed_verifications_backup_pre_cleanup
as table public.drawing_seed_verifications;

do $$
declare
  constraint_row record;
begin
  for constraint_row in
    select conname
    from pg_constraint
    where conrelid = 'public.drawing_seed_verifications'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%attempt_type%'
  loop
    execute format(
      'alter table public.drawing_seed_verifications drop constraint %I',
      constraint_row.conname
    );
  end loop;
end
$$;

alter table public.drawing_seed_verifications
  add constraint drawing_seed_verifications_attempt_type_cleanup_check
  check (
    attempt_type in (
      'owner_test',
      'blind_impostor',
      'informed_forgery',
      'near_miss',
      'true_wrong_shape',
      'bad_sample',
      'wrong_shape',
      'concept_variant',
      'ambiguous',
      'step_up_component'
    )
  );

create index if not exists drawing_seed_verifications_cleanup_type_idx
  on public.drawing_seed_verifications(attempt_type, enrollment_id);

commit;

notify pgrst, 'reload schema';

-- Review progress:
-- select attempt_type, count(*)
-- from public.drawing_seed_verifications
-- group by attempt_type
-- order by attempt_type;
