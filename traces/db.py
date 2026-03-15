"""SQLite schema and queries for the learning evidence database."""
import sqlite3
import json
from pathlib import Path
from datetime import datetime


DB_PATH = Path(__file__).parent.parent / "evidence.db"


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None):
    """Create all tables if they don't exist."""
    conn = get_db(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS skill_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            company TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            version TEXT,
            content TEXT NOT NULL,
            diff_from_prev TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS memory_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            company TEXT NOT NULL,
            memory_type TEXT NOT NULL CHECK(memory_type IN ('memory', 'user')),
            content TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            heartbeat_num INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS heartbeat_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            company TEXT NOT NULL,
            session_id TEXT,
            prev_session_id TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            duration_ms INTEGER DEFAULT 0,
            task_summary TEXT,
            heartbeat_num INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS skill_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            company TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            context TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learning_milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            company TEXT NOT NULL,
            milestone_type TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_skill_snap_agent ON skill_snapshots(agent_id, skill_name);
        CREATE INDEX IF NOT EXISTS idx_memory_snap_agent ON memory_snapshots(agent_id, memory_type);
        CREATE INDEX IF NOT EXISTS idx_heartbeat_agent ON heartbeat_metrics(agent_id);
        CREATE INDEX IF NOT EXISTS idx_milestones_company ON learning_milestones(company);
        CREATE INDEX IF NOT EXISTS idx_milestones_type ON learning_milestones(milestone_type);
    """)
    conn.commit()
    conn.close()


# --- Query helpers for the dashboard ---

def get_skill_timeline(db_path: Path | None = None, company: str | None = None) -> list[dict]:
    """Get all skill snapshots ordered by time, optionally filtered by company."""
    conn = get_db(db_path)
    if company:
        rows = conn.execute(
            "SELECT * FROM skill_snapshots WHERE company = ? ORDER BY created_at",
            (company,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM skill_snapshots ORDER BY created_at"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_memory_growth(db_path: Path | None = None, agent_id: str | None = None) -> list[dict]:
    """Get memory snapshots showing growth over time."""
    conn = get_db(db_path)
    if agent_id:
        rows = conn.execute(
            "SELECT * FROM memory_snapshots WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memory_snapshots ORDER BY created_at"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_heartbeat_trends(db_path: Path | None = None, company: str | None = None) -> list[dict]:
    """Get heartbeat metrics for efficiency trend analysis."""
    conn = get_db(db_path)
    if company:
        rows = conn.execute(
            "SELECT * FROM heartbeat_metrics WHERE company = ? ORDER BY created_at",
            (company,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM heartbeat_metrics ORDER BY created_at"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_milestones(db_path: Path | None = None, limit: int = 50) -> list[dict]:
    """Get recent learning milestones."""
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM learning_milestones ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("evidence_json"):
            d["evidence"] = json.loads(d["evidence_json"])
        result.append(d)
    return result


def get_session_chains(db_path: Path | None = None) -> dict[str, list[dict]]:
    """Get session chains per agent (session_id linkage via prev_session_id)."""
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT agent_id, session_id, prev_session_id, heartbeat_num, created_at "
        "FROM heartbeat_metrics WHERE session_id IS NOT NULL ORDER BY created_at"
    ).fetchall()
    conn.close()

    chains: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        agent = d["agent_id"]
        chains.setdefault(agent, []).append(d)
    return chains


def get_dashboard_summary(db_path: Path | None = None, since: str | None = None) -> dict:
    """Aggregate stats for the dashboard header. Optional `since` ISO timestamp to filter."""
    conn = get_db(db_path)
    summary = {}
    since_clause = f"AND created_at >= '{since}'" if since else ""
    since_clause_s = f"AND s.created_at >= '{since}'" if since else ""
    for company in ("blitz", "craft"):
        row = conn.execute(f"""
            SELECT
                COUNT(DISTINCT s.skill_name) as total_skills,
                COUNT(DISTINCT s.id) as skill_versions,
                (SELECT COUNT(*) FROM heartbeat_metrics WHERE company = ? {since_clause}) as total_heartbeats,
                (SELECT COALESCE(SUM(cost_usd), 0) FROM heartbeat_metrics WHERE company = ? {since_clause}) as total_cost,
                (SELECT COUNT(*) FROM learning_milestones WHERE company = ? {since_clause}) as total_milestones
            FROM skill_snapshots s WHERE s.company = ? {since_clause_s}
        """, (company, company, company, company)).fetchone()
        summary[company] = dict(row)
    conn.close()
    return summary
