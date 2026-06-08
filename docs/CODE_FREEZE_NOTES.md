# Drawing-RNG Code Freeze Notes

This build closes three final algorithmic gaps before FAR/FRR/EER evaluation.

## A. Soft-token allocation in fuzzy projection

The human-readable stroke tokens remain hard-quantized, but the fuzzy extractor now adds soft direction features when segment angles fall within ±3 degrees of an 8-way direction bucket boundary. This reduces SimHash bit cliffs caused by tiny angular jitter without rewriting the tokenizer.

## B. Minimum complexity gate

`analyze_enrollment()` rejects low-complexity drawing seeds when:

- `len(canonical_tokens) < 8`, or
- `unique_direction_token_count < 3`.

These are prototype guardrails against trivial graphical secrets like dots, single lines, and extremely simple one-stroke inputs.

## C. Locked BCH parameters

The hardened fuzzy extractor now freezes BCH evaluation parameters:

- GF(2^10)
- correction limit t = 32
- hidden secret size = 16 bytes / 128 bits

If `bchlib` cannot instantiate the locked configuration, the system falls back to repetition mode and reports `ecc_warning` in the helper metadata. This makes evaluation runs comparable.

## Logging

Enrollment analysis and redraw verification are automatically logged when Supabase is configured.

Environment variables:

```text
ENROLLMENT_TABLE=drawing_seed_enrollments
VERIFICATION_TABLE=drawing_seed_verifications
AUTO_LOG_ENROLLMENTS=1
AUTO_LOG_VERIFICATIONS=1
```

Run `sql/004_verification_attempts_schema.sql` before using verification logging.
