"""Evidence collector: watches HERMES_HOME dirs and captures learning evidence."""
import os
import re
import sqlite3
import json
import difflib
from pathlib import Path
from datetime import datetime

from traces.db import get_db, DB_PATH


# Agent ID → HERMES_HOME mapping
AGENT_HOMES: dict[str, Path] = {}
# Agent ID → company mapping
AGENT_COMPANIES: dict[str, str] = {}
# Agent ID → heartbeat counter
HEARTBEAT_COUNTERS: dict[str, int] = {}


def register_agent(agent_id: str, company: str, hermes_home: Path):
    """Register an agent for evidence tracking."""
    AGENT_HOMES[agent_id] = hermes_home
    AGENT_COMPANIES[agent_id] = company
    HEARTBEAT_COUNTERS[agent_id] = 0


def _get_bundled_skill_names(hermes_home: Path) -> set[str]:
    """Read .bundled_manifest to get names of pre-installed skills (to exclude them)."""
    manifest = hermes_home / "skills" / ".bundled_manifest"
    if not manifest.exists():
        return set()
    bundled = set()
    for line in manifest.read_text().splitlines():
        # Format: "skill-name:hash"
        if ":" in line:
            bundled.add(line.split(":")[0].strip())
    return bundled


def _get_skills(hermes_home: Path) -> dict[str, dict]:
    """Scan HERMES_HOME/skills/ for agent-written SKILL.md files (excludes bundled)."""
    skills_dir = hermes_home / "skills"
    if not skills_dir.exists():
        return {}

    bundled = _get_bundled_skill_names(hermes_home)

    skills = {}
    for skill_md in skills_dir.rglob("SKILL.md"):
        skill_name = skill_md.parent.name
        # Skip bundled/pre-installed skills
        if skill_name in bundled:
            continue
        content = skill_md.read_text(errors="replace")

        # Extract version from YAML frontmatter
        version = "1.0.0"
        version_match = re.search(r"^version:\s*(.+)$", content, re.MULTILINE)
        if version_match:
            version = version_match.group(1).strip().strip('"\'')

        skills[skill_name] = {"content": content, "version": version}
    return skills


def _get_memory(hermes_home: Path) -> dict[str, str]:
    """Read MEMORY.md and USER.md content."""
    memories = {}
    for name, filename in [("memory", "MEMORY.md"), ("user", "USER.md")]:
        path = hermes_home / "memories" / filename
        if path.exists():
            memories[name] = path.read_text(errors="replace")
        else:
            memories[name] = ""
    return memories


def _compute_diff(old_content: str, new_content: str) -> str:
    """Compute a unified diff between old and new content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile="previous", tofile="current")
    return "".join(diff)


def snapshot_skills(agent_id: str, db_path: Path | None = None):
    """Snapshot current skills for an agent, detect new/changed skills."""
    if agent_id not in AGENT_HOMES:
        return

    hermes_home = AGENT_HOMES[agent_id]
    company = AGENT_COMPANIES[agent_id]
    current_skills = _get_skills(hermes_home)
    conn = get_db(db_path)

    for skill_name, skill_data in current_skills.items():
        # Get the most recent snapshot for this skill
        prev = conn.execute(
            "SELECT content, version FROM skill_snapshots "
            "WHERE agent_id = ? AND skill_name = ? ORDER BY created_at DESC LIMIT 1",
            (agent_id, skill_name),
        ).fetchone()

        if prev is None:
            # New skill created
            conn.execute(
                "INSERT INTO skill_snapshots (agent_id, company, skill_name, version, content, diff_from_prev) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, company, skill_name, skill_data["version"], skill_data["content"], None),
            )
            conn.execute(
                "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
                "VALUES (?, ?, 'skill_created', ?, ?)",
                (
                    agent_id, company,
                    f"Agent {agent_id} created skill '{skill_name}' v{skill_data['version']}",
                    json.dumps({"skill_name": skill_name, "version": skill_data["version"]}),
                ),
            )
        elif prev["content"] != skill_data["content"]:
            # Skill updated
            diff = _compute_diff(prev["content"], skill_data["content"])
            conn.execute(
                "INSERT INTO skill_snapshots (agent_id, company, skill_name, version, content, diff_from_prev) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, company, skill_name, skill_data["version"], skill_data["content"], diff),
            )
            conn.execute(
                "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
                "VALUES (?, ?, 'skill_improved', ?, ?)",
                (
                    agent_id, company,
                    f"Agent {agent_id} improved skill '{skill_name}' to v{skill_data['version']}",
                    json.dumps({
                        "skill_name": skill_name,
                        "old_version": prev["version"],
                        "new_version": skill_data["version"],
                        "diff_lines": len(diff.splitlines()),
                    }),
                ),
            )

    conn.commit()
    conn.close()


def snapshot_memory(agent_id: str, db_path: Path | None = None):
    """Snapshot current memory state for an agent."""
    if agent_id not in AGENT_HOMES:
        return

    hermes_home = AGENT_HOMES[agent_id]
    company = AGENT_COMPANIES[agent_id]
    heartbeat_num = HEARTBEAT_COUNTERS.get(agent_id, 0)
    memories = _get_memory(hermes_home)
    conn = get_db(db_path)

    for mem_type, content in memories.items():
        char_count = len(content)

        # Check previous snapshot
        prev = conn.execute(
            "SELECT char_count FROM memory_snapshots "
            "WHERE agent_id = ? AND memory_type = ? ORDER BY created_at DESC LIMIT 1",
            (agent_id, mem_type),
        ).fetchone()

        conn.execute(
            "INSERT INTO memory_snapshots (agent_id, company, memory_type, content, char_count, heartbeat_num) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, company, mem_type, content, char_count, heartbeat_num),
        )

        # Detect significant memory growth
        if prev and char_count > prev["char_count"] + 100:
            growth = char_count - prev["char_count"]
            conn.execute(
                "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
                "VALUES (?, ?, 'memory_growth', ?, ?)",
                (
                    agent_id, company,
                    f"Agent {agent_id} memory grew by {growth} chars ({mem_type})",
                    json.dumps({
                        "memory_type": mem_type,
                        "prev_chars": prev["char_count"],
                        "new_chars": char_count,
                        "growth": growth,
                    }),
                ),
            )

    conn.commit()
    conn.close()


def record_heartbeat(
    agent_id: str,
    session_id: str | None = None,
    prev_session_id: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    task_summary: str = "",
    db_path: Path | None = None,
):
    """Record metrics for a completed heartbeat."""
    company = AGENT_COMPANIES.get(agent_id, "unknown")
    HEARTBEAT_COUNTERS[agent_id] = HEARTBEAT_COUNTERS.get(agent_id, 0) + 1
    heartbeat_num = HEARTBEAT_COUNTERS[agent_id]

    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO heartbeat_metrics "
        "(agent_id, company, session_id, prev_session_id, tokens_in, tokens_out, "
        "cost_usd, duration_ms, task_summary, heartbeat_num) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (agent_id, company, session_id, prev_session_id, tokens_in, tokens_out,
         cost_usd, duration_ms, task_summary, heartbeat_num),
    )

    # Detect efficiency gains (compare to earlier heartbeats)
    if heartbeat_num >= 3:
        rows = conn.execute(
            "SELECT tokens_out FROM heartbeat_metrics "
            "WHERE agent_id = ? ORDER BY created_at DESC LIMIT 5",
            (agent_id,),
        ).fetchall()
        if len(rows) >= 3:
            recent_avg = sum(r["tokens_out"] for r in rows[:2]) / 2
            earlier_avg = sum(r["tokens_out"] for r in rows[2:]) / len(rows[2:])
            if earlier_avg > 0 and recent_avg < earlier_avg * 0.7:
                conn.execute(
                    "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
                    "VALUES (?, ?, 'efficiency_gain', ?, ?)",
                    (
                        agent_id, company,
                        f"Agent {agent_id} using 30%+ fewer tokens (learning efficiency)",
                        json.dumps({
                            "recent_avg_tokens": recent_avg,
                            "earlier_avg_tokens": earlier_avg,
                            "reduction_pct": round((1 - recent_avg / earlier_avg) * 100, 1),
                        }),
                    ),
                )

    conn.commit()
    conn.close()


def detect_skill_usage(agent_id: str, output_text: str, db_path: Path | None = None):
    """Scan agent output for references to known skill names."""
    if agent_id not in AGENT_HOMES:
        return

    company = AGENT_COMPANIES[agent_id]
    hermes_home = AGENT_HOMES[agent_id]
    known_skills = _get_skills(hermes_home)
    conn = get_db(db_path)

    output_lower = output_text.lower()
    for skill_name in known_skills:
        # Use word boundary matching to avoid false positives (e.g. "git" matching "digit")
        if re.search(r'\b' + re.escape(skill_name.lower()) + r'\b', output_lower):
            conn.execute(
                "INSERT INTO skill_usage_events (agent_id, company, skill_name, context) "
                "VALUES (?, ?, ?, ?)",
                (agent_id, company, skill_name, output_text[:500]),
            )
            conn.execute(
                "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
                "VALUES (?, ?, 'skill_used', ?, ?)",
                (
                    agent_id, company,
                    f"Agent {agent_id} referenced skill '{skill_name}' in output",
                    json.dumps({"skill_name": skill_name}),
                ),
            )

    conn.commit()
    conn.close()


def detect_cross_agent_learning(db_path: Path | None = None):
    """Detect when agents in the same company use skills created by other agents."""
    conn = get_db(db_path)

    # Find skills used by agents who didn't create them
    rows = conn.execute("""
        SELECT u.agent_id as user_agent, u.skill_name, u.company,
               s.agent_id as creator_agent
        FROM skill_usage_events u
        JOIN skill_snapshots s ON u.skill_name = s.skill_name AND u.company = s.company
        WHERE u.agent_id != s.agent_id
        AND NOT EXISTS (
            SELECT 1 FROM learning_milestones
            WHERE milestone_type = 'cross_agent_learning'
            AND agent_id = u.agent_id
            AND json_extract(evidence_json, '$.skill_name') = u.skill_name
        )
        GROUP BY u.agent_id, u.skill_name
    """).fetchall()

    for r in rows:
        conn.execute(
            "INSERT INTO learning_milestones (agent_id, company, milestone_type, description, evidence_json) "
            "VALUES (?, ?, 'cross_agent_learning', ?, ?)",
            (
                r["user_agent"], r["company"],
                f"Agent {r['user_agent']} used skill '{r['skill_name']}' created by {r['creator_agent']}",
                json.dumps({
                    "skill_name": r["skill_name"],
                    "creator": r["creator_agent"],
                    "user": r["user_agent"],
                }),
            ),
        )

    conn.commit()
    conn.close()


def collect_all(agent_id: str, output_text: str = "", db_path: Path | None = None, **heartbeat_kwargs):
    """Run all collection steps after a heartbeat completes."""
    snapshot_skills(agent_id, db_path)
    snapshot_memory(agent_id, db_path)
    record_heartbeat(agent_id, db_path=db_path, **heartbeat_kwargs)
    if output_text:
        detect_skill_usage(agent_id, output_text, db_path)
    detect_cross_agent_learning(db_path)
