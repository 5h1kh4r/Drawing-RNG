# Roadmap

## Done

- Stroke-token encoder v0.3
- Prompt doodle collection UI
- Supabase storage
- Rendered drawing review tools
- Clean dataset analysis runner
- Initial enrollment stability framing

## Next

1. Host the new `/enroll` page with a separate Supabase project/table.
2. Collect a true enrollment dataset: each person creates a drawing seed and redraws it 3 times.
3. Use `experiments/run_clean_dataset_tests.py` for clean prompt data.
4. Add a dedicated `experiments/run_enrollment_dataset.py` if the enrollment table grows.
5. Add a stronger Drawing Seed Quality Meter.

## Later

- Common-doodle strength meter
- Better canonical seed recovery
- Fuzzy extractor / secure sketch research
- Password-manager-style browser extension demo
