"""Additively restore verified JSON snapshots into the active database.

The command is a dry-run unless ``--apply`` is supplied. It never deletes or
updates current leads, staging data, or scraper runs. Before applying changes,
it writes a complete backup of every table it can modify.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
from dotenv import dotenv_values

try:
    from scripts.migrate_railway_leads import (
        TABLES as LEAD_TABLES,
        fetch_rows,
        identity_key,
        insert_child,
        insert_lead,
        write_backup,
    )
    from scripts.migrate_railway_remaining_data import (
        merge_runs_and_records,
    )
except ModuleNotFoundError:  # Supports ``python scripts/...py``.
    from migrate_railway_leads import (
        TABLES as LEAD_TABLES,
        fetch_rows,
        identity_key,
        insert_child,
        insert_lead,
        write_backup,
    )
    from migrate_railway_remaining_data import merge_runs_and_records


ROOT = Path(__file__).resolve().parents[1]
RESTORE_TABLES = (*LEAD_TABLES, "scraper_runs", "scraper_run_records")


def normalized_text(value: object) -> str:
    return str(value or "").strip().casefold()


def normalized_name(row: dict) -> str:
    stored = normalized_text(row.get("name_normalized"))
    if stored:
        return stored
    return re.sub(r"[^\w\s]", "", normalized_text(row.get("name"))).strip()


def normalized_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if len(digits) >= 7 else ""


def normalized_url(value: object) -> str:
    raw = normalized_text(value)
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(candidate)
    return f"{parsed.netloc.removeprefix('www.')}{parsed.path.rstrip('/')}"


def similar_name(left: dict, right: dict) -> bool:
    left_name = normalized_name(left)
    right_name = normalized_name(right)
    if left_name in right_name or right_name in left_name:
        return True
    return SequenceMatcher(None, left_name, right_name).ratio() >= 0.72


@dataclass(frozen=True)
class LeadRestorationPlan:
    id_map: dict[int, int]
    to_insert: list[dict]


def plan_lead_restoration(
    snapshot_leads: list[dict],
    current_leads: list[dict],
) -> LeadRestorationPlan:
    """Match existing entities conservatively and return only absent rows."""

    by_identity: dict[tuple, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    by_source_url: dict[str, list[dict]] = defaultdict(list)
    by_website: dict[str, list[dict]] = defaultdict(list)
    by_email: dict[str, list[dict]] = defaultdict(list)
    by_phone: dict[str, list[dict]] = defaultdict(list)

    for row in current_leads:
        by_identity[identity_key(row)].append(row)
        by_name[normalized_name(row)].append(row)
        for index, key in (
            (by_source_url, normalized_url(row.get("source_url"))),
            (by_website, normalized_url(row.get("website"))),
            (by_email, normalized_text(row.get("email"))),
            (by_phone, normalized_phone(row.get("phone"))),
        ):
            if key:
                index[key].append(row)

    id_map: dict[int, int] = {}
    to_insert: list[dict] = []
    for row in snapshot_leads:
        candidates = by_identity[identity_key(row)]
        if candidates:
            id_map[row["id"]] = min(candidate["id"] for candidate in candidates)
            continue

        candidates = by_name[normalized_name(row)]
        if candidates:
            id_map[row["id"]] = min(candidate["id"] for candidate in candidates)
            continue

        stable_matches: dict[int, dict] = {}
        for index, key in (
            (by_source_url, normalized_url(row.get("source_url"))),
            (by_website, normalized_url(row.get("website"))),
            (by_email, normalized_text(row.get("email"))),
        ):
            if key:
                stable_matches.update(
                    {candidate["id"]: candidate for candidate in index[key]}
                )
        phone = normalized_phone(row.get("phone"))
        if phone:
            stable_matches.update(
                {
                    candidate["id"]: candidate
                    for candidate in by_phone[phone]
                    if similar_name(row, candidate)
                }
            )
        if len(stable_matches) == 1:
            id_map[row["id"]] = next(iter(stable_matches))
            continue
        if len(stable_matches) > 1:
            raise RuntimeError(
                f"Snapshot lead {row['id']} has multiple stable matches: "
                f"{sorted(stable_matches)}"
            )
        to_insert.append(row)

    return LeadRestorationPlan(id_map=id_map, to_insert=to_insert)


def child_key(row: dict) -> tuple:
    return tuple(
        (column, str(value) if value is not None else None)
        for column, value in sorted(row.items())
        if column not in {"id", "lead_id"}
    )


def load_snapshot(path: Path) -> dict[str, list[dict]]:
    return json.loads(path.read_text(encoding="utf-8"))


def restore(
    connection,
    lead_snapshot: dict[str, list[dict]],
    run_snapshots: list[dict[str, list[dict]]],
    *,
    apply: bool,
) -> dict:
    current = {table: fetch_rows(connection, table) for table in RESTORE_TABLES}
    lead_plan = plan_lead_restoration(
        lead_snapshot["leads"],
        current["leads"],
    )
    result = {
        "current_before": {table: len(rows) for table, rows in current.items()},
        "lead_snapshot": {
            table: len(lead_snapshot[table]) for table in LEAD_TABLES
        },
        "leads_matched": len(lead_plan.id_map),
        "leads_to_insert": len(lead_plan.to_insert),
        "run_snapshots": [
            {
                "runs": len(snapshot["scraper_runs"]),
                "records": len(snapshot["scraper_run_records"]),
            }
            for snapshot in run_snapshots
        ],
    }
    if not apply:
        connection.rollback()
        return result

    backup_dir = (
        Path.home()
        / "SalesIntelligenceMigrationBackups"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    result["target_backup"] = str(
        write_backup("supabase-before-recovery", current, backup_dir)
    )

    id_map = dict(lead_plan.id_map)
    inserted_children = {"lead_notes": 0, "lead_events": 0}
    inserted_runs = 0
    inserted_records = 0
    try:
        with connection.cursor() as cursor:
            for row in lead_plan.to_insert:
                id_map[row["id"]] = insert_lead(cursor, row)

            for table in ("lead_notes", "lead_events"):
                existing = {
                    (row["lead_id"], child_key(row)) for row in current[table]
                }
                for row in lead_snapshot[table]:
                    target_lead_id = id_map[row["lead_id"]]
                    key = (target_lead_id, child_key(row))
                    if key in existing:
                        continue
                    insert_child(cursor, table, row, target_lead_id)
                    existing.add(key)
                    inserted_children[table] += 1

            target_runs = current["scraper_runs"]
            target_records = current["scraper_run_records"]
            for snapshot in run_snapshots:
                run_count, record_count, _ = merge_runs_and_records(
                    cursor,
                    snapshot["scraper_runs"],
                    snapshot["scraper_run_records"],
                    target_runs,
                    target_records,
                )
                inserted_runs += run_count
                inserted_records += record_count
                target_runs = fetch_rows(connection, "scraper_runs")
                target_records = fetch_rows(connection, "scraper_run_records")

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    result["applied"] = {
        "leads_inserted": len(lead_plan.to_insert),
        **{f"{table}_inserted": count for table, count in inserted_children.items()},
        "runs_inserted": inserted_runs,
        "run_records_inserted": inserted_records,
    }
    result["current_after"] = {
        table: len(fetch_rows(connection, table)) for table in RESTORE_TABLES
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lead-snapshot", required=True, type=Path)
    parser.add_argument(
        "--run-snapshot",
        required=True,
        type=Path,
        action="append",
        dest="run_snapshots",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL") or dotenv_values(
        ROOT / ".env"
    ).get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    connection = psycopg2.connect(database_url, connect_timeout=20)
    try:
        result = restore(
            connection,
            load_snapshot(args.lead_snapshot),
            [load_snapshot(path) for path in args.run_snapshots],
            apply=args.apply,
        )
    finally:
        connection.close()
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", **result},
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
