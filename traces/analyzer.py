"""Analyzer: computes trends and generates learning reports from evidence.db."""
import json
from pathlib import Path

from traces.db import (
    get_db,
    get_skill_timeline,
    get_memory_growth,
    get_heartbeat_trends,
    get_milestones,
    get_session_chains,
    get_dashboard_summary,
    DB_PATH,
)


def compute_efficiency_trend(company: str, db_path: Path | None = None) -> list[dict]:
    """Compute tokens-per-heartbeat trend for a company."""
    heartbeats = get_heartbeat_trends(db_path, company)
    trend = []
    for hb in heartbeats:
        trend.append({
            "agent_id": hb["agent_id"],
            "heartbeat_num": hb["heartbeat_num"],
            "tokens_total": hb["tokens_in"] + hb["tokens_out"],
            "cost_usd": hb["cost_usd"],
            "duration_ms": hb["duration_ms"],
            "created_at": hb["created_at"],
        })
    return trend


def compute_skill_velocity(company: str, db_path: Path | None = None) -> dict:
    """How fast are skills being created and improved."""
    timeline = get_skill_timeline(db_path, company)
    if not timeline:
        return {"total_skills": 0, "total_versions": 0, "improvements": 0}

    unique_skills = set()
    improvements = 0
    for snap in timeline:
        if snap["diff_from_prev"]:
            improvements += 1
        unique_skills.add(snap["skill_name"])

    return {
        "total_skills": len(unique_skills),
        "total_versions": len(timeline),
        "improvements": improvements,
    }


def compute_memory_trajectory(agent_id: str, db_path: Path | None = None) -> list[dict]:
    """Memory char count over time for an agent."""
    snapshots = get_memory_growth(db_path, agent_id)
    trajectory = []
    for snap in snapshots:
        trajectory.append({
            "heartbeat_num": snap["heartbeat_num"],
            "memory_type": snap["memory_type"],
            "char_count": snap["char_count"],
            "created_at": snap["created_at"],
        })
    return trajectory


def generate_learning_report(db_path: Path | None = None) -> dict:
    """Generate a full learning evidence report for the dashboard."""
    summary = get_dashboard_summary(db_path)
    milestones = get_milestones(db_path, limit=100)
    session_chains = get_session_chains(db_path)

    blitz_efficiency = compute_efficiency_trend("blitz", db_path)
    craft_efficiency = compute_efficiency_trend("craft", db_path)
    blitz_skill_vel = compute_skill_velocity("blitz", db_path)
    craft_skill_vel = compute_skill_velocity("craft", db_path)

    # Milestone breakdown by type
    milestone_counts: dict[str, int] = {}
    for m in milestones:
        t = m["milestone_type"]
        milestone_counts[t] = milestone_counts.get(t, 0) + 1

    return {
        "summary": summary,
        "milestones": milestones,
        "milestone_counts": milestone_counts,
        "session_chains": {
            agent: [{"session_id": s["session_id"], "heartbeat": s["heartbeat_num"]} for s in chain]
            for agent, chain in session_chains.items()
        },
        "efficiency": {
            "blitz": blitz_efficiency,
            "craft": craft_efficiency,
        },
        "skill_velocity": {
            "blitz": blitz_skill_vel,
            "craft": craft_skill_vel,
        },
    }
