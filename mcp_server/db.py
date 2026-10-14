"""Database access for the Campus Customs MCP server.

Every agent reaches the shop data through this module, so the connection rules
live in one place:
- Only the working copy (data/campus_customs_new.db) is ever opened. The original
  data/campus_customs.db is never touched.
- Foreign-key checks are switched on for every connection (SQLite leaves them off).
- The file must already exist. We never let SQLite create an empty database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

HW5_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = HW5_DIR / "data" / "campus_customs_new.db"
ORIGINAL_DB_PATH = HW5_DIR / "data" / "campus_customs.db"

# The 9 tables in the shop database, grouped by the agent that owns them
# (see outputs/harness.md, section 1).
TABLE_OWNERS: dict[str, str] = {
    "desk": "boss",
    "tickets": "boss",
    "inventory": "inventory",
    "pricing": "inventory",
    "vendors": "accounting",
    "invoices": "accounting",
    "payments": "accounting",
    "cash_accounts": "accounting",
    "leases": "facilities",
}

# Tables the operations team adds on top of the shop data (Problem 3).
# `approvals` is written by the agents' "prepare" tools and decided only by a human.
OPS_TABLE_OWNERS: dict[str, str] = {
    "vendor_products": "accounting",
    "purchase_orders": "accounting",
    "approvals": "human",
    "activity_log": "boss",
    "ticket_updates": "boss",
    "customer_messages": "customer_service",
    "agent_messages": "boss",
}

OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_products (
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    sku       TEXT    NOT NULL REFERENCES pricing(sku),
    PRIMARY KEY (vendor_id, sku)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id            INTEGER PRIMARY KEY,
    vendor_id     INTEGER NOT NULL REFERENCES vendors(id),
    sku           TEXT    NOT NULL,
    size          TEXT    NOT NULL,
    qty           INTEGER NOT NULL CHECK (qty > 0),
    unit_cost     REAL    NOT NULL,
    total         REAL    NOT NULL,
    expected_date TEXT    NOT NULL,
    ticket_id     INTEGER REFERENCES tickets(id),
    status        TEXT    NOT NULL DEFAULT 'pending_approval'
                  CHECK (status IN ('pending_approval', 'approved', 'rejected', 'received')),
    reason        TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    FOREIGN KEY (sku, size) REFERENCES inventory(sku, size)
);

CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY,
    kind          TEXT    NOT NULL
                  CHECK (kind IN ('invoice_payment', 'rent_payment', 'purchase_order', 'discount')),
    ref_id        INTEGER NOT NULL,   -- invoice id / lease id / purchase order id / ticket id
    amount        REAL    NOT NULL,
    summary       TEXT    NOT NULL,
    details       TEXT    NOT NULL,   -- JSON
    requested_by  TEXT    NOT NULL,   -- agent that prepared it
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by    TEXT,
    decision_note TEXT,
    decided_at    TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id            INTEGER PRIMARY KEY,
    agent         TEXT NOT NULL,
    action        TEXT NOT NULL,
    details       TEXT NOT NULL,      -- JSON
    business_date TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_updates (
    id          INTEGER PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    old_status  TEXT    NOT NULL,
    new_status  TEXT    NOT NULL,
    decision    TEXT    NOT NULL,   -- the Boss's final call, in words
    agent       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_messages (
    id          INTEGER PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    to_name     TEXT    NOT NULL,
    subject     TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'approved', 'sent', 'discarded')),
    drafted_by  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL,   -- one team run (e.g. one ticket)
    ticket_id   INTEGER,
    from_agent  TEXT    NOT NULL,
    to_agent    TEXT    NOT NULL,
    task        TEXT    NOT NULL,
    response    TEXT,
    status      TEXT    NOT NULL DEFAULT 'working'
                CHECK (status IN ('working', 'done', 'refused', 'error')),
    depth       INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    finished_at TEXT
);

-- Which vendor restocks which product. Inferred from vendors.specialty:
-- Bulldog Print Co (apparel reprint) makes the hoodie, tee and cap;
-- Elm City Gifts (mugs and small goods) supplies the mug. QuickShip CT is a courier.
INSERT OR IGNORE INTO vendor_products (vendor_id, sku) VALUES
    (1, 'CC-HOOD-NAVY'), (1, 'CC-TEE-WHITE'), (1, 'CC-HAT-BLUE'), (2, 'CC-MUG-CREST');
"""


def db_path() -> Path:
    """Path of the working database. Can be overridden with CAMPUS_DB_PATH (used by tests)."""
    path = Path(os.environ.get("CAMPUS_DB_PATH", DEFAULT_DB_PATH)).resolve()
    if path == ORIGINAL_DB_PATH.resolve():
        raise RuntimeError("Refusing to open the original database; use data/campus_customs_new.db.")
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    return path


@contextmanager
def connect(*, read_only: bool = True) -> Iterator[sqlite3.Connection]:
    """Open the working database. Read-only unless the caller asks to write.

    Writes are committed when the block finishes and rolled back on any error.
    """
    mode = "ro" if read_only else "rw"  # "rw" (not "rwc") so a missing file is never created
    conn = sqlite3.connect(f"{db_path().as_uri()}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        if not read_only:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_ops_tables() -> None:
    """Create the operations tables if they don't exist yet. Safe to run every start-up."""
    with connect(read_only=False) as conn:
        conn.executescript(OPS_SCHEMA)
        # Content seals (Problem 5 safety): added to tables that may already exist.
        for table in ("approvals", "customer_messages"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "content_sha256" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN content_sha256 TEXT")


def check_database() -> dict:
    """Confirm the database opens and has all 9 shop tables. Returns a short summary."""
    with connect() as conn:
        found = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = sorted(set(TABLE_OWNERS) - found)
        if missing:
            raise RuntimeError(f"Database is missing tables: {', '.join(missing)}")
        today = conn.execute("SELECT date_today FROM desk").fetchone()["date_today"]
        tables = [t for t in {**TABLE_OWNERS, **OPS_TABLE_OWNERS} if t in found]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    return {"path": str(db_path()), "business_date": today, "row_counts": counts}


# --- Helpers shared by the agent tools -------------------------------------------------


def business_date(conn: sqlite3.Connection) -> date:
    """The shop's "today" (desk.date_today). Agents use this, never the computer clock."""
    return date.fromisoformat(conn.execute("SELECT date_today FROM desk").fetchone()["date_today"])


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def money(value: float) -> float:
    return round(float(value), 2)


def log_activity(conn: sqlite3.Connection, agent: str, action: str, details: dict) -> None:
    """Record what an agent (or the human) changed, for the dashboard's activity feed."""
    conn.execute(
        "INSERT INTO activity_log (agent, action, details, business_date, created_at) VALUES (?, ?, ?, ?, ?)",
        (agent, action, json.dumps(details), business_date(conn).isoformat(), now_utc()),
    )


def create_approval(
    conn: sqlite3.Connection, *, kind: str, ref_id: int, amount: float, summary: str,
    details: dict, requested_by: str,
) -> dict:
    """Queue a request for the human. Nothing is paid, ordered or discounted until they approve."""
    from mcp_server.safety import content_hash
    details_json = json.dumps(details)
    seal = content_hash(kind, ref_id, money(amount), summary, details_json)  # re-checked when the human decides
    cur = conn.execute(
        "INSERT INTO approvals (kind, ref_id, amount, summary, details, requested_by, created_at, content_sha256)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, ref_id, money(amount), summary, details_json, requested_by, now_utc(), seal),
    )
    log_activity(conn, requested_by, f"requested_{kind}", {"approval_id": cur.lastrowid, "summary": summary})
    return approval_row(conn, cur.lastrowid)


def approval_row(conn: sqlite3.Connection, approval_id: int) -> dict:
    row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    out = dict(row)
    out["details"] = json.loads(out["details"])
    return out


def pending_approval(conn: sqlite3.Connection, kind: str, ref_id: int) -> dict | None:
    """An approval of this kind for this record that is still waiting on the human, if any."""
    row = conn.execute(
        "SELECT id FROM approvals WHERE kind = ? AND ref_id = ? AND status = 'pending'", (kind, ref_id)
    ).fetchone()
    return approval_row(conn, row["id"]) if row else None


def pending_payment_total(conn: sqlite3.Connection) -> float:
    """Cash already promised to payments waiting for approval."""
    return money(conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM approvals"
        " WHERE status = 'pending' AND kind IN ('invoice_payment', 'rent_payment')"
    ).fetchone()[0])


def cash_balance(conn: sqlite3.Connection, account: str = "checking") -> float:
    row = conn.execute("SELECT balance FROM cash_accounts WHERE name = ?", (account,)).fetchone()
    if row is None:
        raise ValueError(f"No cash account named {account!r}")
    return money(row["balance"])
