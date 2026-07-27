"""Short-lived, retryable PostgreSQL transactions for scraper processes."""

import logging
import os
import time
from collections.abc import Callable
from typing import Any

import psycopg2


logger = logging.getLogger(__name__)
TRANSIENT_CONNECTION_ERRORS = (
    psycopg2.InterfaceError,
    psycopg2.OperationalError,
)


def _connect(fallback_config: dict[str, Any]):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(**fallback_config)


def run_transaction(
    operation: Callable[[Any], Any],
    *,
    fallback_config: dict[str, Any],
    operation_name: str,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
):
    """Run one idempotent DB operation in a fresh, bounded transaction.

    Scrapers spend minutes in browser I/O. Opening the database connection only
    here prevents Supabase's transaction pool from expiring an idle session.
    A new connection is used for each retry after a transient disconnect.
    """
    for attempt in range(1, max_attempts + 1):
        connection = None
        try:
            connection = _connect(fallback_config)
            result = operation(connection)
            connection.commit()
            return result
        except TRANSIENT_CONNECTION_ERRORS:
            if connection is not None:
                try:
                    connection.rollback()
                except TRANSIENT_CONNECTION_ERRORS:
                    pass
                try:
                    connection.close()
                except TRANSIENT_CONNECTION_ERRORS:
                    pass
                connection = None
            if attempt == max_attempts:
                raise
            delay = base_delay_seconds * attempt
            logger.warning(
                "%s lost its database connection; retrying with a fresh "
                "connection in %.1fs (attempt %s/%s)",
                operation_name,
                delay,
                attempt + 1,
                max_attempts,
            )
            time.sleep(delay)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except TRANSIENT_CONNECTION_ERRORS:
                    pass
