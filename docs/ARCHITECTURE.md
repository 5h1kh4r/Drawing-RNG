# Architecture

## Workflows

### Prompt Doodle Collection

Used for category-level research: star, heart, spiral, triangle, etc.

```text
canvas strokes → /api/save_prompt_sample → stroke_samples
```

### Drawing Seed Enrollment

Used for the actual Drawing-RNG idea.

```text
attempt 1 + attempt 2 + attempt 3
→ encode with strict/balanced/tolerant profiles
→ choose best profile
→ choose central attempt
→ derive demo outputs
→ optionally save to drawing_seed_enrollments
```

## Why enrollment matters

Hashing one drawing directly is too brittle. A redraw changes the token string. Enrollment lets the system measure whether the user's remembered drawing is stable enough before using it as seed material.

## Current demo outputs

- 32-byte seed hex
- domain-specific demo password
- deterministic avatar palette

These are prototype outputs, not production security primitives.
