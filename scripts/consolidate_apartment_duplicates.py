"""Dry-run-first consolidation for apartment leads with the same property name.

Usage:
    python scripts/consolidate_apartment_duplicates.py
    python scripts/consolidate_apartment_duplicates.py --apply

The default mode is read-only and prints a JSON report. Apply only after the
report has been reviewed. The selected canonical lead retains its pipeline
state; phone, email, website, and owner information are filled from duplicate
records before notes, events, and outreach history are re-linked.
"""

import argparse
import json
import os

import psycopg2
from psycopg2.extras import RealDictCursor


DB = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "nzetu_db"),
    "user": os.getenv("POSTGRES_USER", "nzetu"),
    "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
}

GROUPS_SQL = """
WITH grouped AS (
    SELECT
        LOWER(TRIM(REGEXP_REPLACE(name, '[^\\w\\s]', '', 'g'))) AS normalized_name,
        ARRAY_AGG(id ORDER BY
            (status <> 'new') DESC,
            (assigned_to IS NOT NULL) DESC,
            updated_at DESC NULLS LAST,
            created_at ASC NULLS LAST,
            id ASC
        ) AS lead_ids,
        ARRAY_AGG(DISTINCT area) FILTER (WHERE area IS NOT NULL) AS areas,
        COUNT(*) FILTER (WHERE phone IS NOT NULL) AS phone_count,
        COUNT(*) FILTER (WHERE email IS NOT NULL) AS email_count,
        COUNT(*) FILTER (WHERE website IS NOT NULL) AS website_count
    FROM leads
    WHERE lead_type = 'apartment'
      AND NULLIF(TRIM(name), '') IS NOT NULL
    GROUP BY 1
    HAVING COUNT(*) > 1
)
SELECT normalized_name, lead_ids, areas, phone_count, email_count, website_count
FROM grouped
ORDER BY normalized_name
"""


def duplicate_groups(cursor):
    cursor.execute(GROUPS_SQL)
    return cursor.fetchall()


def apply_group(cursor, group):
    canonical_id = group["lead_ids"][0]
    duplicate_ids = group["lead_ids"][1:]
    cursor.execute("""
        UPDATE leads target
        SET
            phone = COALESCE(target.phone, source.phone),
            email = COALESCE(target.email, source.email),
            website = COALESCE(target.website, source.website),
            owner_name = COALESCE(target.owner_name, source.owner_name),
            updated_at = NOW()
        FROM (
            SELECT
                (SELECT phone FROM leads WHERE id = ANY(%(lead_ids)s) AND phone IS NOT NULL ORDER BY id LIMIT 1) AS phone,
                (SELECT email FROM leads WHERE id = ANY(%(lead_ids)s) AND email IS NOT NULL ORDER BY id LIMIT 1) AS email,
                (SELECT website FROM leads WHERE id = ANY(%(lead_ids)s) AND website IS NOT NULL ORDER BY id LIMIT 1) AS website,
                (SELECT owner_name FROM leads WHERE id = ANY(%(lead_ids)s) AND owner_name IS NOT NULL ORDER BY id LIMIT 1) AS owner_name
        ) source
        WHERE target.id = %(canonical_id)s
    """, {"canonical_id": canonical_id, "lead_ids": group["lead_ids"]})
    for table in ("lead_notes", "lead_events", "email_outreach"):
        cursor.execute(
            f"UPDATE {table} SET lead_id = %(canonical_id)s WHERE lead_id = ANY(%(duplicate_ids)s)",
            {"canonical_id": canonical_id, "duplicate_ids": duplicate_ids},
        )
    cursor.execute("DELETE FROM leads WHERE id = ANY(%s)", (duplicate_ids,))
    return len(duplicate_ids)


def main():
    parser = argparse.ArgumentParser(description="Consolidate duplicate apartment leads")
    parser.add_argument("--apply", action="store_true", help="apply the reviewed consolidation")
    args = parser.parse_args()

    with psycopg2.connect(**DB) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            groups = duplicate_groups(cursor)
            report = {
                "mode": "apply" if args.apply else "dry-run",
                "duplicate_groups": len(groups),
                "duplicate_leads": sum(len(group["lead_ids"]) - 1 for group in groups),
                "groups": groups,
            }
            if args.apply:
                report["deleted_leads"] = sum(apply_group(cursor, group) for group in groups)
            print(json.dumps(report, indent=2, default=list))
            if not args.apply:
                connection.rollback()


if __name__ == "__main__":
    main()
