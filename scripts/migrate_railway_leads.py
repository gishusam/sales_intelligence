"""One-time, lossless Railway-to-Supabase lead migration.

Dry-run by default. Pass ``--apply`` to write after creating local JSON backups.
The legacy Railway URL is read without importing or executing scraper_agent.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
TABLES = ("leads", "lead_notes", "lead_events")
TARGET_LEAD_COLUMNS = (
    "name", "owner_name", "owner_type", "phone", "email", "website", "area",
    "lead_type", "source", "source_url", "score", "status", "lead_quality",
    "assigned_to", "last_contacted", "contact_attempts", "follow_up_date",
    "email_sent_at", "ai_score", "ai_score_reason", "ai_scored_at",
    "contact_person", "contact_person_role", "promoted_at", "created_at", "updated_at",
)


def normalized_text(value: object) -> str:
    return str(value or "").strip().casefold()


def normalized_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if digits else ""


def identity_key(row: dict) -> tuple[str, str, str, str]:
    """Match an already-scraped target lead without collapsing source duplicates."""
    return (
        normalized_text(row.get("name")),
        normalized_text(row.get("area")),
        normalized_text(row.get("lead_type")),
        normalized_phone(row.get("phone")),
    )


def fetch_rows(conn, table: str) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY id")
        return [dict(row) for row in cur.fetchall()]


def plan_matches(source_leads: list[dict], target_leads: list[dict]) -> dict[int, int]:
    target_by_key: dict[tuple[str, str, str, str], int] = {}
    for row in target_leads:
        key = identity_key(row)
        if key in target_by_key:
            raise RuntimeError(f"Target contains an ambiguous lead identity: {key[:3]}")
        target_by_key[key] = row["id"]

    matches = {
        row["id"]: target_by_key[identity_key(row)]
        for row in source_leads
        if identity_key(row) in target_by_key
    }
    collisions = [target_id for target_id, count in Counter(matches.values()).items() if count > 1]
    if collisions:
        raise RuntimeError(f"Multiple Railway leads map to target lead IDs: {collisions}")
    return matches


def write_backup(label: str, rows: dict[str, list[dict]], backup_dir: Path) -> Path:
    path = backup_dir / f"{label}.json"
    path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    return path


def insert_lead(cur, row: dict) -> int:
    columns = TARGET_LEAD_COLUMNS
    placeholders = ", ".join(f"%({column})s" for column in columns)
    cur.execute(
        f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
        {column: row.get(column) for column in columns},
    )
    return cur.fetchone()[0]


def insert_child(cur, table: str, row: dict, target_lead_id: int) -> None:
    columns = [column for column in row if column != "id"]
    payload = dict(row)
    payload["lead_id"] = target_lead_id
    cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
        f"({', '.join(f'%({column})s' for column in columns)})",
        payload,
    )


def migrate(source, target, apply: bool) -> dict:
    source_rows = {table: fetch_rows(source, table) for table in TABLES}
    target_rows = {table: fetch_rows(target, table) for table in TABLES}
    matches = plan_matches(source_rows["leads"], target_rows["leads"])
    expected_total = len(target_rows["leads"]) + len(source_rows["leads"]) - len(matches)

    result = {
        "source_leads": len(source_rows["leads"]),
        "target_leads_before": len(target_rows["leads"]),
        "already_present": len(matches),
        "leads_to_insert": len(source_rows["leads"]) - len(matches),
        "expected_target_total": expected_total,
        "notes_to_insert": len(source_rows["lead_notes"]),
        "events_to_insert": len(source_rows["lead_events"]),
    }
    if not apply:
        target.rollback()
        return result

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "SalesIntelligenceMigrationBackups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    result["source_backup"] = str(write_backup("railway-source", source_rows, backup_dir))
    result["target_backup"] = str(write_backup("supabase-before", target_rows, backup_dir))

    id_map = dict(matches)
    try:
        with target.cursor() as cur:
            for row in source_rows["leads"]:
                if row["id"] not in id_map:
                    id_map[row["id"]] = insert_lead(cur, row)
            for table in ("lead_notes", "lead_events"):
                for row in source_rows[table]:
                    insert_child(cur, table, row, id_map[row["lead_id"]])

            cur.execute("SELECT COUNT(*) FROM leads")
            actual_total = cur.fetchone()[0]
            if actual_total != expected_total or len(id_map) != len(source_rows["leads"]):
                raise RuntimeError(
                    f"Migration invariant failed: expected {expected_total}, got {actual_total}, "
                    f"mapped {len(id_map)}/{len(source_rows['leads'])}"
                )
        target.commit()
    except Exception:
        target.rollback()
        raise

    result["target_leads_after"] = actual_total
    result["backup_directory"] = str(backup_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the migration")
    args = parser.parse_args()

    source_url = os.getenv("RAILWAY_DATABASE_URL")
    target_url = os.getenv("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not source_url:
        raise RuntimeError("RAILWAY_DATABASE_URL is required")
    if not target_url:
        raise RuntimeError("DATABASE_URL is required")

    source = psycopg2.connect(source_url)
    target = psycopg2.connect(target_url)
    try:
        result = migrate(source, target, args.apply)
    finally:
        source.close()
        target.close()
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **result}, indent=2))


if __name__ == "__main__":
    main()
