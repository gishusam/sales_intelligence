#!/usr/bin/env python3
"""
create_user.py — Admin script to create and manage user accounts.

Usage:
    POSTGRES_HOST=localhost python create_user.py create \
        --name "Max Omondi" \
        --email "max@nyumbazetu.com" \
        --password "Nyumba2024!" \
        --role sales

    POSTGRES_HOST=localhost python create_user.py list
    POSTGRES_HOST=localhost python create_user.py deactivate --id 3
"""

import argparse
import os
import sys

import psycopg2
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "nzetu_db"),
    user=os.getenv("POSTGRES_USER", "nzetu"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)


def get_conn():
    return psycopg2.connect(**DB)


def ensure_columns(conn):
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE users
                ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()
        """)
        conn.commit()


def create_user(name, email, password, role):
    if role not in ("sales", "admin", "manager"):
        print("Error: role must be sales, admin, or manager")
        sys.exit(1)

    hashed = pwd_context.hash(password)
    conn   = get_conn()
    ensure_columns(conn)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (
                    name, email, password_hash, role,
                    is_active, must_change_password
                )
                VALUES (%s, %s, %s, %s, TRUE, TRUE)
                RETURNING id, name, email, role
            """, (name, email.lower().strip(), hashed, role))
            row = cur.fetchone()
            conn.commit()

        print(f"\n✓ User created")
        print(f"  ID:       {row[0]}")
        print(f"  Name:     {row[1]}")
        print(f"  Email:    {row[2]}")
        print(f"  Role:     {row[3]}")
        print(f"  Password: {password}  ← share this with the rep")
        print(f"  Note:     Rep must change password on first login\n")

    except psycopg2.errors.UniqueViolation:
        print(f"\n✗ Email '{email}' is already registered\n")
        sys.exit(1)
    finally:
        conn.close()


def list_users():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, email, role, is_active, must_change_password
                FROM users ORDER BY id
            """)
            rows = cur.fetchall()

        if not rows:
            print("\nNo users found.\n")
            return

        print(f"\n{'ID':<5} {'Name':<20} {'Email':<30} {'Role':<10} {'Active':<8} {'Must Change'}")
        print("─" * 80)
        for r in rows:
            active      = "✓" if r[4] else "✗"
            must_change = "⚠ yes" if r[5] else "no"
            print(f"{r[0]:<5} {r[1]:<20} {r[2]:<30} {r[3]:<10} {active:<8} {must_change}")
        print()
    finally:
        conn.close()


def deactivate_user(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = FALSE WHERE id = %s RETURNING name",
                (user_id,)
            )
            row = cur.fetchone()
            conn.commit()

        if row:
            print(f"\n✓ User '{row[0]}' deactivated\n")
        else:
            print(f"\n✗ User ID {user_id} not found\n")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Nyumba Zetu users")
    sub    = parser.add_subparsers(dest="command")

    c = sub.add_parser("create")
    c.add_argument("--name",     required=True)
    c.add_argument("--email",    required=True)
    c.add_argument("--password", required=True)
    c.add_argument("--role",     default="sales",
                   choices=["sales", "admin", "manager"])

    sub.add_parser("list")

    d = sub.add_parser("deactivate")
    d.add_argument("--id", type=int, required=True)

    args = parser.parse_args()

    if args.command == "create":
        create_user(args.name, args.email, args.password, args.role)
    elif args.command == "list":
        list_users()
    elif args.command == "deactivate":
        deactivate_user(args.id)
    else:
        parser.print_help()
