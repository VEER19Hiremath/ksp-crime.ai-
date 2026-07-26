"""Seeds one demo login per role (bcrypt-hashed passwords — can't be plain SQL).
Run after 03_auth.sql. Usage (from backend/, venv active):
    python ../database/seed_users.py
"""
import os
import sys

import bcrypt
import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.config import get_settings  # noqa: E402

DEMO_USERS = [
    ("investigator", "investigator123", "Investigator", "Ramesh Kulkarni"),
    ("sho", "sho123456", "SHO", "Suresh Patil"),
    ("dsp", "dsp1234567", "DSP", "Anitha Rao"),
    ("analyst", "analyst12345", "Analyst", "Data Analyst"),
    ("admin", "admin1234567", "Administrator", "System Administrator"),
]


def main():
    settings = get_settings()
    conn = psycopg.connect(settings.database_url)
    cur = conn.cursor()
    for username, password, role, full_name in DEMO_USERS:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            """INSERT INTO app_user (username, password_hash, full_name, role)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
            (username, password_hash, full_name, role),
        )
    conn.commit()
    conn.close()
    print(f"Seeded {len(DEMO_USERS)} demo users:")
    for username, password, role, _ in DEMO_USERS:
        print(f"  {username} / {password}  ({role})")


if __name__ == "__main__":
    main()
