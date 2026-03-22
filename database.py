import hashlib
import os
import secrets
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Backend detection — PostgreSQL when DATABASE_URL is set, SQLite otherwise
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
# Render provides postgres:// but psycopg2 requires postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import errors as pg_errors
    _P = "%s"          # psycopg2 placeholder
else:
    import sqlite3
    _P = "?"           # sqlite3 placeholder
    _DB_PATH = os.path.join(os.path.dirname(__file__), "poker.db")


def get_db():
    if _USE_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _exec(conn, sql, params=()):
    """Execute a single statement, return cursor."""
    sql = sql.replace("?", _P)
    if _USE_PG:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def init_db():
    conn = get_db()
    if _USE_PG:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS poker_tables (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES players(id),
                invite_code TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS table_members (
                table_id INTEGER NOT NULL REFERENCES poker_tables(id),
                player_id INTEGER NOT NULL REFERENCES players(id),
                joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (table_id, player_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS game_results (
                id SERIAL PRIMARY KEY,
                player_id INTEGER NOT NULL REFERENCES players(id),
                table_id INTEGER REFERENCES poker_tables(id),
                amount REAL NOT NULL,
                game_date TEXT NOT NULL,
                notes TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        # Migration: add table_id column if it doesn't exist yet
        cur.execute("""
            DO $$ BEGIN
                ALTER TABLE game_results ADD COLUMN table_id INTEGER REFERENCES poker_tables(id);
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
        conn.commit()
        cur.close()
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS poker_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES players(id),
                invite_code TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS table_members (
                table_id INTEGER NOT NULL REFERENCES poker_tables(id),
                player_id INTEGER NOT NULL REFERENCES players(id),
                joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (table_id, player_id)
            );
            CREATE TABLE IF NOT EXISTS game_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                table_id INTEGER REFERENCES poker_tables(id),
                amount REAL NOT NULL,
                game_date TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (player_id) REFERENCES players(id)
            );
        """)
        # Migration: add table_id to existing game_results if column is missing
        try:
            conn.execute("ALTER TABLE game_results ADD COLUMN table_id INTEGER REFERENCES poker_tables(id)")
            conn.commit()
        except Exception:
            pass  # Column already exists
        conn.commit()
    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# --- Player queries ---

def create_player(username, password):
    conn = get_db()
    try:
        _exec(conn, "INSERT INTO players (username, password_hash) VALUES (?, ?)",
              (username, hash_password(password)))
        conn.commit()
        return True, None
    except (Exception,) as e:
        if _USE_PG:
            conn.rollback()
        if "unique" in str(e).lower() or "UNIQUE" in str(e):
            return False, "Username already taken."
        raise
    finally:
        conn.close()


def get_player_by_username(username):
    conn = get_db()
    row = _exec(conn, "SELECT * FROM players WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def verify_player(username, password):
    player = get_player_by_username(username)
    if player and player["password_hash"] == hash_password(password):
        return player
    return None


def get_player_by_id(player_id):
    conn = get_db()
    row = _exec(conn, "SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    conn.close()
    return row


# --- Table queries ---

def _generate_invite_code():
    return secrets.token_urlsafe(8)


def create_table(name, creator_id):
    """Create a table and add the creator as a member. Returns (table_id, invite_code)."""
    conn = get_db()
    for _ in range(5):
        code = _generate_invite_code()
        try:
            if _USE_PG:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO poker_tables (name, created_by, invite_code) VALUES (%s, %s, %s) RETURNING id",
                    (name, creator_id, code)
                )
                table_id = cur.fetchone()["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO poker_tables (name, created_by, invite_code) VALUES (?, ?, ?)",
                    (name, creator_id, code)
                )
                table_id = cur.lastrowid
            _exec(conn, "INSERT INTO table_members (table_id, player_id) VALUES (?, ?)", (table_id, creator_id))
            conn.commit()
            conn.close()
            return table_id, code
        except Exception as e:
            if "unique" in str(e).lower():
                continue
            conn.close()
            raise
    conn.close()
    raise RuntimeError("Could not generate a unique invite code")


def get_table_by_id(table_id):
    conn = get_db()
    row = _exec(conn, "SELECT * FROM poker_tables WHERE id = ?", (table_id,)).fetchone()
    conn.close()
    return row


def get_table_by_invite_code(code):
    conn = get_db()
    row = _exec(conn, "SELECT * FROM poker_tables WHERE invite_code = ?", (code,)).fetchone()
    conn.close()
    return row


def join_table(table_id, player_id):
    """Add player to table. Returns True if joined, False if already a member."""
    conn = get_db()
    try:
        _exec(conn, "INSERT INTO table_members (table_id, player_id) VALUES (?, ?)", (table_id, player_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def is_table_member(table_id, player_id):
    conn = get_db()
    row = _exec(conn,
        "SELECT 1 FROM table_members WHERE table_id = ? AND player_id = ?",
        (table_id, player_id)).fetchone()
    conn.close()
    return row is not None


def get_tables_for_player(player_id):
    conn = get_db()
    rows = _exec(conn, """
        SELECT pt.* FROM poker_tables pt
        JOIN table_members tm ON pt.id = tm.table_id
        WHERE tm.player_id = ?
        ORDER BY pt.created_at DESC
    """, (player_id,)).fetchall()
    conn.close()
    return rows


def get_table_member_count(table_id):
    conn = get_db()
    row = _exec(conn, "SELECT COUNT(*) AS c FROM table_members WHERE table_id = ?", (table_id,)).fetchone()
    conn.close()
    return row["c"]


# --- Game result queries ---

def log_result(player_id, amount, game_date, notes, table_id):
    conn = get_db()
    _exec(conn,
          "INSERT INTO game_results (player_id, table_id, amount, game_date, notes) VALUES (?, ?, ?, ?, ?)",
          (player_id, table_id, amount, game_date, notes))
    conn.commit()
    conn.close()


def get_result_by_id(result_id):
    conn = get_db()
    row = _exec(conn, "SELECT * FROM game_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    return row


def update_result(result_id, player_id, amount, game_date, notes):
    conn = get_db()
    _exec(conn,
          "UPDATE game_results SET amount=?, game_date=?, notes=? WHERE id=? AND player_id=?",
          (amount, game_date, notes, result_id, player_id))
    conn.commit()
    conn.close()


def delete_result(result_id, player_id):
    conn = get_db()
    _exec(conn, "DELETE FROM game_results WHERE id=? AND player_id=?", (result_id, player_id))
    conn.commit()
    conn.close()


def get_results_for_player(player_id, table_id):
    conn = get_db()
    rows = _exec(conn,
        "SELECT * FROM game_results WHERE player_id = ? AND table_id = ? ORDER BY game_date ASC, created_at ASC",
        (player_id, table_id)).fetchall()
    conn.close()
    return rows


def get_all_results_ordered(table_id):
    conn = get_db()
    rows = _exec(conn, """
        SELECT gr.*, p.username
        FROM game_results gr
        JOIN players p ON gr.player_id = p.id
        WHERE gr.table_id = ?
        ORDER BY gr.game_date ASC, gr.created_at ASC
    """, (table_id,)).fetchall()
    conn.close()
    return rows


def get_leaderboard(table_id):
    conn = get_db()
    rows = _exec(conn, """
        SELECT p.id, p.username,
               COALESCE(SUM(gr.amount), 0) AS net_total,
               COUNT(gr.id) AS games_played
        FROM players p
        JOIN table_members tm ON p.id = tm.player_id AND tm.table_id = ?
        LEFT JOIN game_results gr ON p.id = gr.player_id AND gr.table_id = ?
        GROUP BY p.id, p.username
        ORDER BY net_total DESC
    """, (table_id, table_id)).fetchall()
    conn.close()
    return rows


def get_summary_stats(player_id, table_id):
    since_7  = (date.today() - timedelta(days=7)).isoformat()
    since_30 = (date.today() - timedelta(days=30)).isoformat()

    conn = get_db()
    all_time = _exec(conn,
        "SELECT COALESCE(SUM(amount), 0) AS v, COUNT(*) AS c FROM game_results WHERE player_id = ? AND table_id = ?",
        (player_id, table_id)).fetchone()
    last7 = _exec(conn,
        "SELECT COALESCE(SUM(amount), 0) AS v, COUNT(*) AS c FROM game_results WHERE player_id = ? AND table_id = ? AND game_date >= ?",
        (player_id, table_id, since_7)).fetchone()
    last30 = _exec(conn,
        "SELECT COALESCE(SUM(amount), 0) AS v, COUNT(*) AS c FROM game_results WHERE player_id = ? AND table_id = ? AND game_date >= ?",
        (player_id, table_id, since_30)).fetchone()
    best  = _exec(conn, "SELECT MAX(amount) AS v FROM game_results WHERE player_id = ? AND table_id = ?", (player_id, table_id)).fetchone()
    worst = _exec(conn, "SELECT MIN(amount) AS v FROM game_results WHERE player_id = ? AND table_id = ?", (player_id, table_id)).fetchone()
    conn.close()

    return {
        "all_time_net":   all_time["v"],
        "all_time_games": all_time["c"],
        "last7_net":      last7["v"],
        "last7_games":    last7["c"],
        "last30_net":     last30["v"],
        "last30_games":   last30["c"],
        "best_session":   best["v"] or 0,
        "worst_session":  worst["v"] or 0,
    }


def delete_demo_data():
    """Remove demo accounts and their data if they exist."""
    demo_users = ("alice", "bob", "charlie", "diana")
    conn = get_db()
    for username in demo_users:
        row = _exec(conn, "SELECT id FROM players WHERE username = ?", (username,)).fetchone()
        if row:
            _exec(conn, "DELETE FROM game_results WHERE player_id = ?", (row["id"],))
            _exec(conn, "DELETE FROM table_members WHERE player_id = ?", (row["id"],))
            _exec(conn, "DELETE FROM players WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
