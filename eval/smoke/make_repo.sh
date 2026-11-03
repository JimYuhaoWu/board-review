#!/usr/bin/env bash
# Build the smoke-test repository: a base commit and a head commit with planted bugs.
# Usage: make_repo.sh <empty-or-missing-dir>
# The expected findings are in eval/smoke/README.md. Never copy that file into the repo.
set -euo pipefail

dir=${1:?usage: make_repo.sh <dir>}
if [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
  echo "refusing: $dir exists and is not empty" >&2
  exit 1
fi
mkdir -p "$dir"
cd "$dir"
git init -q -b main
git config core.autocrlf false
git config user.name smoke
git config user.email smoke@example.invalid

mkdir -p shop tests
# ---------- base ----------
cat > shop/__init__.py <<'EOF'
EOF
cat > shop/db.py <<'EOF'
import sqlite3


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER, total REAL)")
    return conn
EOF
cat > shop/users.py <<'EOF'
_DB = None


def set_db(conn):
    global _DB
    _DB = conn


def get_user(user_id):
    """Return the user row for user_id, or None."""
    return _DB.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
EOF
cat > shop/reports.py <<'EOF'
from shop.users import get_user


def user_label(user_id):
    user = get_user(user_id)
    if user is None:
        return "unknown user"
    return f"{user[1]} <{user[2]}>"
EOF
cat > tests/test_users.py <<'EOF'
from shop import db, users


def test_get_user():
    conn = db.connect()
    conn.execute("INSERT INTO users VALUES (1, 'Ann', 'ann@example.com')")
    users.set_db(conn)
    assert users.get_user(1)[1] == "Ann"
EOF
git add -A
git commit -q -m "Initial shop package"

# ---------- head ----------
cat > shop/users.py <<'EOF'
PAGE_SIZE = 20


def get_user(db, user_id):
    """Return the user row for user_id, or None."""
    return db.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()


def search_users(db, name, page=1):
    """Search users by name. Pages start at 1; each page holds PAGE_SIZE users."""
    offset = page * PAGE_SIZE
    query = f"SELECT id, name, email FROM users WHERE name LIKE '%{name}%' LIMIT {PAGE_SIZE} OFFSET {offset}"
    return db.execute(query).fetchall()


def unique_emails(rows):
    """Return the distinct emails in rows, in first-seen order."""
    seen = []
    for row in rows:
        if row[2] not in seen:
            seen.append(row[2])
    return seen
EOF
cat > shop/orders.py <<'EOF'
def average_order_value(db, user_id):
    """Average total of a user's orders."""
    totals = [r[0] for r in db.execute("SELECT total FROM orders WHERE user_id = ?", (user_id,))]
    return sum(totals) / len(totals)


def order_count(db, user_id):
    """Number of orders a user has placed."""
    return db.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()[0]
EOF
cat > tests/test_users.py <<'EOF'
from shop import db, users


def test_get_user():
    conn = db.connect()
    conn.execute("INSERT INTO users VALUES (1, 'Ann', 'ann@example.com')")
    assert users.get_user(conn, 1)[1] == "Ann"
EOF
git add -A
git commit -q -m "Add user search with pagination and order statistics

Users can now be searched by name. Results are paginated: pages start at 1
and hold 20 users each. get_user now takes the database connection
explicitly instead of a module-level global. Adds average order value and
order count per user, and a helper to list distinct emails."

echo "base=$(git rev-parse HEAD~1)"
echo "head=$(git rev-parse HEAD)"
