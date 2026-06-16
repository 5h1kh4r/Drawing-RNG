-- Dataset review / relabeling support for Drawing-RNG pilot analysis.
-- Keeps raw operator labels intact while allowing a cleaned experimental view.

alter table public.drawing_seed_verifications
add column if not exists original_attempt_type text,
add column if not exists relabeled_attempt_type text,
add column if not exists review_status text default 'unreviewed',
add column if not exists label_confidence double precision,
add column if not exists label_notes text,
add column if not exists reviewed_at timestamptz,
add column if not exists token_score_weighted double precision,
add column if not exists token_bigram_score double precision;

create index if not exists drawing_seed_verifications_relabel_idx
on public.drawing_seed_verifications (coalesce(relabeled_attempt_type, attempt_type));

create index if not exists drawing_seed_verifications_review_status_idx
on public.drawing_seed_verifications (review_status);
