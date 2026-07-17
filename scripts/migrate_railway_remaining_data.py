"""Merge the remaining Railway application data into Supabase exactly once.

Leads, notes, and events were migrated separately.  This script adds users,
staging tables, scraper-run history, and run-audit rows without replacing the
newer Cloud Run records.  It is dry-run by default and can be run repeatedly.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2.extras import RealDictCursor, execute_values

try:
    from scripts.migrate_railway_leads import fetch_rows, write_backup
except ModuleNotFoundError:  # Supports `python scripts/...py` as well as pytest.
    from migrate_railway_leads import fetch_rows, write_backup


ROOT = Path(__file__).resolve().parents[1]
STAGING_KEYS = {
    "apartment_staging": ("normalized_name", "search_area"),
    "developer_staging": ("developer_name",),
    "google_places_leads": ("business_name", "area"),
}
TABLES = ("users", *STAGING_KEYS, "scraper_runs", "scraper_run_records")


def normalize(value: object) -> str:
    return str(value or "").strip().casefold()


def row_key(row: dict, columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize(row.get(column)) for column in columns)


def run_key(row: dict) -> tuple[str, tuple[str, ...], str]:
    return (
        normalize(row.get("scraper_type")),
        tuple(row.get("areas") or ()),
        str(row.get("started_at")),
    )


def audit_key(row: dict) -> tuple[object, ...]:
    return tuple(row.get(column) for column in (
        "name", "area", "phone", "website", "category", "outcome", "reason", "created_at"
    ))


def insert_staging(cur, table: str, rows: list[dict]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    key_columns = STAGING_KEYS[table]
    columns = [column for column in rows[0] if column != "id"]
    updates = [
        f"{column} = COALESCE({table}.{column}, EXCLUDED.{column})"
        for column in columns if column not in key_columns
    ]
    statement = f"""
        INSERT INTO {table} ({', '.join(columns)}) VALUES %s
        ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {', '.join(updates)}
        RETURNING (xmax = 0) AS inserted
    """
    values = [[row.get(column) for column in columns] for row in rows]
    inserted = 0
    for start in range(0, len(values), 100):
        execute_values(cur, statement, values[start:start + 100], page_size=100)
        inserted += sum(bool(row[0]) for row in cur.fetchall())
    return inserted, len(rows) - inserted


def merge_users(cur, rows: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    for row in rows:
        cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (row["email"],))
        exists = cur.fetchone()
        cur.execute(
            """
            INSERT INTO users (
                name, email, password_hash, role, is_active, must_change_password, created_at, updated_at
            ) VALUES (%(name)s, %(email)s, %(password_hash)s, %(role)s, %(is_active)s,
                %(must_change_password)s, %(created_at)s, %(updated_at)s)
            ON CONFLICT (email) DO UPDATE SET
                name = EXCLUDED.name,
                password_hash = EXCLUDED.password_hash,
                role = EXCLUDED.role,
                is_active = EXCLUDED.is_active,
                must_change_password = EXCLUDED.must_change_password,
                created_at = LEAST(users.created_at, EXCLUDED.created_at),
                updated_at = GREATEST(users.updated_at, EXCLUDED.updated_at)
            """,
            row,
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def merge_runs_and_records(cur, source_runs: list[dict], source_records: list[dict], target_runs: list[dict], target_records: list[dict]) -> tuple[int, int, int]:
    target_by_key = {run_key(row): row["id"] for row in target_runs}
    run_map: dict[int, int] = {}
    inserted_runs = 0
    run_columns = [column for column in source_runs[0] if column != "id"] if source_runs else []
    for row in source_runs:
        key = run_key(row)
        if key in target_by_key:
            run_map[row["id"]] = target_by_key[key]
            continue
        placeholders = ", ".join(f"%({column})s" for column in run_columns)
        cur.execute(
            f"INSERT INTO scraper_runs ({', '.join(run_columns)}) VALUES ({placeholders}) RETURNING id",
            {column: row.get(column) for column in run_columns},
        )
        target_id = cur.fetchone()[0]
        target_by_key[key] = target_id
        run_map[row["id"]] = target_id
        inserted_runs += 1

    existing_by_run: dict[int, set[tuple[object, ...]]] = {}
    for row in target_records:
        existing_by_run.setdefault(row["run_id"], set()).add(audit_key(row))
    record_columns = [column for column in source_records[0] if column != "id"] if source_records else []
    inserted_records = 0
    for row in source_records:
        target_run_id = run_map[row["run_id"]]
        key = audit_key(row)
        if key in existing_by_run.setdefault(target_run_id, set()):
            continue
        payload = {column: row.get(column) for column in record_columns}
        payload["run_id"] = target_run_id
        placeholders = ", ".join(f"%({column})s" for column in record_columns)
        cur.execute(
            f"INSERT INTO scraper_run_records ({', '.join(record_columns)}) VALUES ({placeholders})",
            payload,
        )
        existing_by_run[target_run_id].add(key)
        inserted_records += 1
    return inserted_runs, inserted_records, len(run_map)


def migrate(source, target, apply: bool) -> dict:
    source_rows = {table: fetch_rows(source, table) for table in TABLES}
    target_rows = {table: fetch_rows(target, table) for table in TABLES}
    result = {
        "source": {table: len(rows) for table, rows in source_rows.items()},
        "target_before": {table: len(rows) for table, rows in target_rows.items()},
    }
    if not apply:
        return result

    backup_dir = Path.home() / "SalesIntelligenceMigrationBackups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    result["source_backup"] = str(write_backup("railway-remaining-source", source_rows, backup_dir))
    result["target_backup"] = str(write_backup("supabase-before-remaining", target_rows, backup_dir))
    try:
        with target.cursor() as cur:
            users_inserted, users_updated = merge_users(cur, source_rows["users"])
            result["users"] = {"inserted": users_inserted, "updated": users_updated}
            result["staging"] = {}
            for table in STAGING_KEYS:
                inserted, matched = insert_staging(cur, table, source_rows[table])
                result["staging"][table] = {"inserted": inserted, "matched": matched}
            inserted_runs, inserted_records, mapped_runs = merge_runs_and_records(
                cur, source_rows["scraper_runs"], source_rows["scraper_run_records"],
                target_rows["scraper_runs"], target_rows["scraper_run_records"],
            )
            result["runs"] = {"inserted": inserted_runs, "mapped": mapped_runs, "records_inserted": inserted_records}
        target.commit()
    except Exception:
        target.rollback()
        raise
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source_url = os.getenv("RAILWAY_DATABASE_URL")
    target_url = os.getenv("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not source_url or not target_url:
        raise RuntimeError("RAILWAY_DATABASE_URL and DATABASE_URL are required")
    source = psycopg2.connect(source_url, connect_timeout=20)
    target = psycopg2.connect(target_url, connect_timeout=20)
    try:
        result = migrate(source, target, args.apply)
    finally:
        source.close()
        target.close()
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **result}, indent=2, default=str))


if __name__ == "__main__":
    main()
