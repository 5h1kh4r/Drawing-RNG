#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import create_client


TABLES = {
    "legacy": "drawing_seed_verifications",
    "v2": "draw2seed_v2_verifications",
}

VALID_LABELS = {
    "owner_test",
    "blind_impostor",
    "informed_forgery",
    "near_miss",
    "true_wrong_shape",
    "bad_sample",
}

# Exact decisions from the manual visual review.
DESIRED_LABEL_BY_CASE = {
    1: "near_miss",
    2: "owner_test",
    3: "owner_test",
    4: "owner_test",
    5: "owner_test",
    6: "informed_forgery",
    7: "informed_forgery",
    8: "informed_forgery",
    9: "owner_test",
    10: "owner_test",
    11: "informed_forgery",
    12: "owner_test",
    13: "owner_test",
    14: "owner_test",
    15: "owner_test",
    16: "owner_test",
    17: "owner_test",
    18: "near_miss",
    19: "owner_test",
    20: "owner_test",
    21: "owner_test",
    22: "owner_test",
    23: "near_miss",
    24: "owner_test",
    25: "near_miss",
    26: "near_miss",
    27: "near_miss",
    28: "near_miss",
    29: "owner_test",
    30: "owner_test",
    31: "owner_test",
    32: "near_miss",
    33: "near_miss",
}

# The gallery was sorted by category. This protects against applying case
# numbers to a reordered or regenerated CSV.
EXPECTED_GALLERY_LABEL_BY_CASE = {
    **{case: "blind_impostor" for case in range(1, 4)},
    **{case: "informed_forgery" for case in range(4, 13)},
    **{case: "near_miss" for case in range(13, 32)},
    **{case: "true_wrong_shape" for case in range(32, 34)},
}


def load_env(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def fetch_verification(
    client: Any,
    table: str,
    verification_id: str,
) -> dict[str, Any]:
    response = (
        client.table(table)
        .select("id,enrollment_id,attempt_type,created_at")
        .eq("id", verification_id)
        .limit(1)
        .execute()
    )

    rows = list(response.data or [])

    if not rows:
        raise RuntimeError(
            f"Verification {verification_id} was not found in {table}"
        )

    return rows[0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the manually reviewed labels from the 33-case "
            "Draw2Seed accepted-negative gallery."
        )
    )

    parser.add_argument(
        "--csv",
        default=(
            "dev/results/paper_v227_final/"
            "accepted_negative_review/"
            "accepted_negative_review_static.csv"
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update Supabase. Without this flag, only print the plan.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Permit a current database label that differs from the gallery label.",
    )

    args = parser.parse_args()

    repo = Path.cwd()
    csv_path = Path(args.csv)

    if not csv_path.exists():
        raise SystemExit(f"Review CSV not found: {csv_path}")

    load_env(repo / "dev" / ".env")

    url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not service_key:
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required "
            "in dev/.env or the shell."
        )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if len(rows) != 33:
        raise SystemExit(
            f"Expected exactly 33 gallery rows, but found {len(rows)}. "
            "Do not apply case-number mappings to a different gallery."
        )

    client = create_client(url, service_key)

    plans: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, str]] = set()

    for case_number, csv_row in enumerate(rows, start=1):
        cohort = str(csv_row.get("source_cohort") or "").strip()
        verification_id = str(csv_row.get("verification_id") or "").strip()
        enrollment_id = str(csv_row.get("enrollment_id") or "").strip()
        gallery_label = str(csv_row.get("attempt_type") or "").strip()
        expected_gallery_label = EXPECTED_GALLERY_LABEL_BY_CASE[case_number]
        desired_label = DESIRED_LABEL_BY_CASE[case_number]

        if cohort not in TABLES:
            raise SystemExit(
                f"Case {case_number}: unknown cohort {cohort!r}"
            )

        if not verification_id:
            raise SystemExit(
                f"Case {case_number}: missing verification ID"
            )

        if gallery_label != expected_gallery_label:
            raise SystemExit(
                f"Case {case_number}: gallery label is {gallery_label!r}, "
                f"but {expected_gallery_label!r} was expected. "
                "The CSV order may have changed."
            )

        if desired_label not in VALID_LABELS:
            raise SystemExit(
                f"Case {case_number}: invalid desired label {desired_label!r}"
            )

        unique_key = (cohort, verification_id)

        if unique_key in seen_ids:
            raise SystemExit(
                f"Case {case_number}: duplicate verification ID "
                f"{verification_id}"
            )

        seen_ids.add(unique_key)

        table = TABLES[cohort]
        current = fetch_verification(
            client,
            table,
            verification_id,
        )

        current_label = str(current.get("attempt_type") or "").strip()

        if (
            current_label not in {gallery_label, desired_label}
            and not args.force
        ):
            raise SystemExit(
                f"Case {case_number}: database label is now "
                f"{current_label!r}, while the reviewed gallery contained "
                f"{gallery_label!r}. Refusing to overwrite an unrelated "
                "later change."
            )

        plans.append(
            {
                "case": case_number,
                "cohort": cohort,
                "table": table,
                "enrollment_id": enrollment_id,
                "verification_id": verification_id,
                "gallery_label": gallery_label,
                "current_label": current_label,
                "desired_label": desired_label,
                "change_required": current_label != desired_label,
                "created_at": current.get("created_at"),
            }
        )

    print("\nRelabelling plan")
    print("=" * 110)

    for plan in plans:
        marker = "CHANGE" if plan["change_required"] else "KEEP"

        print(
            f'Case {plan["case"]:2d}  '
            f'{plan["cohort"]:6s}  '
            f'{plan["verification_id"]}  '
            f'{plan["current_label"]:18s} -> '
            f'{plan["desired_label"]:18s}  '
            f'[{marker}]'
        )

    before_counts = Counter(
        plan["current_label"]
        for plan in plans
    )

    after_counts = Counter(
        plan["desired_label"]
        for plan in plans
    )

    print("\nReviewed-case label distribution")

    print("Before:")
    for label, count in sorted(before_counts.items()):
        print(f"  {label:20s} {count:2d}")

    print("After:")
    for label, count in sorted(after_counts.items()):
        print(f"  {label:20s} {count:2d}")

    changes = [
        plan
        for plan in plans
        if plan["change_required"]
    ]

    print(f"\nRows requiring an update: {len(changes)}")

    if not args.apply:
        print("\nDRY RUN ONLY — no Supabase rows were modified.")
        print("Re-run with --apply after checking the plan.")
        return

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    backup_path = (
        repo
        / "dev/results/paper_v227_final"
        / f"accepted_negative_relabel_backup_{timestamp}.json"
    )

    backup_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    backup_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "source_csv": str(csv_path),
                "rows": plans,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nRollback metadata saved to:\n{backup_path}")

    updated = 0

    for plan in changes:
        (
            client.table(plan["table"])
            .update(
                {
                    "attempt_type": plan["desired_label"],
                }
            )
            .eq(
                "id",
                plan["verification_id"],
            )
            .execute()
        )

        updated += 1

        print(
            f'Updated case {plan["case"]:2d}: '
            f'{plan["current_label"]} -> '
            f'{plan["desired_label"]}'
        )

    verification_errors = []

    for plan in plans:
        row = fetch_verification(
            client,
            plan["table"],
            plan["verification_id"],
        )

        actual_label = str(
            row.get("attempt_type") or ""
        ).strip()

        if actual_label != plan["desired_label"]:
            verification_errors.append(
                {
                    "case": plan["case"],
                    "verification_id": plan["verification_id"],
                    "expected": plan["desired_label"],
                    "actual": actual_label,
                }
            )

    if verification_errors:
        raise SystemExit(
            "Some updates could not be verified:\n"
            + json.dumps(
                verification_errors,
                indent=2,
            )
        )

    print(
        f"\nSuccessfully updated and verified "
        f"{updated} verification labels."
    )


if __name__ == "__main__":
    main()
