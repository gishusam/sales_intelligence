#!/usr/bin/env python3
"""Read-only Communications schema preflight.

This tool inspects PostgreSQL metadata catalogues. It never applies the
migration and never mutates application data.
"""

import argparse
import json
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.communications.communications_schema_audit import inspect_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the target PostgreSQL schema for required "
            "Communications objects."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "COMMUNICATIONS_PREFLIGHT_DATABASE_URL",
            "",
        ),
        help=(
            "Target PostgreSQL URL. Defaults to "
            "COMMUNICATIONS_PREFLIGHT_DATABASE_URL."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.database_url:
        print(
            "ERROR: Supply --database-url or set "
            "COMMUNICATIONS_PREFLIGHT_DATABASE_URL.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(
        args.database_url,
        pool_pre_ping=True,
    )

    with Session(engine) as db:
        report = inspect_schema(db=db)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
