"""Manage guard accounts for the alert app.

    python3 guards_admin.py add --email name@thapar.edu --name "Ravi Kumar" [--phone ...]
    python3 guards_admin.py list
    python3 guards_admin.py remove --email name@thapar.edu
    python3 guards_admin.py password --email name@thapar.edu

A guard can create their own login in the app (campus email only), or an admin
creates it here; either way only a row in public.guards, added here, grants
guard access. 'add' reuses a login that already exists. Auth users are shared with the campus
parking app, so 'remove' only revokes guard access and leaves the login intact.
Emails must be on an allowed campus domain (enforced by the auth.users trigger).

Reads DATABASE_URL, SUPABASE_URL and SUPABASE_SERVICE_KEY from .env. The
password is typed at a hidden prompt, so it never lands in shell history.
"""

import argparse
import getpass
import os
import sys

import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()


def env(name):
    v = os.getenv(name)
    if not v:
        sys.exit(f"{name} is not set in .env")
    return v


def ask_password(email):
    pw = getpass.getpass(f"password for {email}: ")
    if len(pw) < 8:
        sys.exit("password must be at least 8 characters")
    if getpass.getpass("repeat password: ") != pw:
        sys.exit("passwords do not match")
    return pw


def add(email, name, phone):
    with psycopg.connect(env("DATABASE_URL")) as conn:
        row = conn.execute("select id from auth.users where lower(email) = %s", (email,)).fetchone()
    if row:  # signed up in one of the apps already: keep their own password
        uid = row[0]
        print(f"using the existing login for {email}")
    else:
        pw = ask_password(email)
        key = env("SUPABASE_SERVICE_KEY")
        r = requests.post(f"{env('SUPABASE_URL')}/auth/v1/admin/users",
                          headers={"apikey": key, "Authorization": f"Bearer {key}"},
                          json={"email": email, "password": pw, "email_confirm": True},
                          timeout=20)
        if r.status_code >= 300:
            sys.exit(f"could not create login ({r.status_code}): {r.text[:200]}")
        uid = r.json()["id"]

    with psycopg.connect(env("DATABASE_URL")) as conn:
        conn.execute("insert into public.guards (id, name, phone) values (%s, %s, %s)",
                     (uid, name, phone))
    print(f"added guard {name} <{email}>")


def list_guards():
    with psycopg.connect(env("DATABASE_URL")) as conn:
        rows = conn.execute("""
            select g.name, u.email, g.on_duty,
                   g.location_at, g.fcm_token is not null, g.platform
            from public.guards g join auth.users u on u.id = g.id
            order by g.name""").fetchall()
    if not rows:
        print("no guards yet")
    for name, email, on_duty, loc_at, has_token, platform in rows:
        seen = loc_at.strftime("%Y-%m-%d %H:%M") if loc_at else "never"
        print(f"  {name:<22} {email:<30} {'ON duty ' if on_duty else 'off duty'}  "
              f"location {seen:<16}  push {'yes' if has_token else 'no '}  {platform or ''}")


def set_password(email):
    """New password for an existing login. Shared with the campus parking app,
    so it changes there too."""
    with psycopg.connect(env("DATABASE_URL")) as conn:
        row = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
    if not row:
        sys.exit(f"no login with email {email}")
    pw = ask_password(email)
    key = env("SUPABASE_SERVICE_KEY")
    r = requests.put(f"{env('SUPABASE_URL')}/auth/v1/admin/users/{row[0]}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"},
                     json={"password": pw}, timeout=20)
    if r.status_code >= 300:
        sys.exit(f"could not change password ({r.status_code}): {r.text[:200]}")
    print(f"password changed for {email}")


def remove(email):
    with psycopg.connect(env("DATABASE_URL")) as conn:
        n = conn.execute("""delete from public.guards
                            where id = (select id from auth.users where email = %s)""",
                         (email,)).rowcount
    print(f"removed guard access for {email}" if n else f"no guard with email {email}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--phone")
    sub.add_parser("list")
    r = sub.add_parser("remove")
    r.add_argument("--email", required=True)
    pw = sub.add_parser("password")
    pw.add_argument("--email", required=True)
    args = ap.parse_args()

    if args.cmd == "add":
        add(args.email.strip().lower(), args.name.strip(), args.phone)
    elif args.cmd == "list":
        list_guards()
    elif args.cmd == "password":
        set_password(args.email.strip().lower())
    else:
        remove(args.email.strip().lower())
