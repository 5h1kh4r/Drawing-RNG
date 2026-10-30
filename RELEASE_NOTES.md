# Draw2Seed v2.4 — guided study overhaul

## Changes

- New clean public **sequential study** with consent, two practice drawings, eight stored enrollment samples, six immediate genuine attempts, three assigned near misses, two unrelated attempts, and optional blind/informed challenges against a separately opted-in target.
- Automatic protocol labels; no volunteer-picked `owner_test` category or scores/acceptance feedback.
- Optional six-drawing delayed follow-up, at least 18 hours after the first session; high-entropy return code (only hash stored).
- All coordinates and browser event timing are stored for later algorithm evaluation. Timing is not used as an identity gate.
- v2.4 experimental consensus verifier: token medoid + two structurally similar enrollment exemplars; accept only on at least two passing strict original token+geometry checks, with unchanged stroke count. Single-reference, token-only, geometry-only, any-reference and DTW **distance** logged as controls. No hidden neural network or gallery training.
- New `d2s24_*` tables and per-attempt study version, leaving historical `d2s_*` tables intact.
- Fresh DEV-only `/dev` dashboard: all eight enrollment drawings inline with all verification drawings; researcher review and history; batched queries and lazy render; CSV export.
- Strict split: PUBLIC has neither `dev_admin.py` nor `dev_ui`, nor a DEV route.

## Important research limitations

- This is an unvalidated experimental decision rule, **not** a proven improvement or biometric identity guarantee.
- No online gallery updates occur in this study; compare adaptive gallery policies offline using longitudinal data and controlled ground truth.
- Browser timestamps and coordinates are attacker-controlled and do not prevent replay.
- Optional challenge availability depends on participants explicitly opting to share their references. One session is not equivalent to one verified unique human.
- New and old cohorts are not directly comparable without accounting for the changed study protocol.
- For minors or vulnerable participants, obtain required guardian/institutional approvals and clarify consent/withdrawal processes.

## Validation performed in this build environment

- Four algorithmic unit tests pass for both variants.
- Python source compilation and JS syntax checks pass.
- Flask/psycopg are not installed in the packaging environment; HTTP integration tests are supplied but **must** be run in WSL before recruitment.
