"""Gladiator Live Scoreboard — FastAPI + SSE server."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from traces.db import (
    init_db, get_dashboard_summary, get_milestones,
    get_skill_timeline, get_heartbeat_trends, get_session_chains,
    get_memory_growth, DB_PATH,
)
from traces.analyzer import generate_learning_report, compute_efficiency_trend

app = FastAPI(title="Gladiator Scoreboard")

PAPERCLIP_URL = "http://localhost:3100/api"
CONFIG_PATH = Path(__file__).parent.parent / "gladiator_config.json"
# State
start_time: float | None = None
competition_active: bool = False
competition_finished: bool = False
competition_winner: dict | None = None
config: dict = {}
intel_history: list[dict] = []
last_known_hb_count: int = 0


def _competition_since() -> str | None:
    """Return UTC timestamp of competition start, or None."""
    if not start_time or not competition_active:
        return None
    return datetime.fromtimestamp(start_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _get_timing() -> dict:
    """Read timing config with sensible defaults."""
    timing = config.get("timing", {})
    return {
        "competition_duration_seconds": timing.get("competition_duration_seconds", 14400),
        "heartbeat_engineer_seconds": timing.get("heartbeat_engineer_seconds", 1800),
        "heartbeat_support_seconds": timing.get("heartbeat_support_seconds", 3600),
    }


@app.on_event("startup")
async def startup():
    global config
    init_db()
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())


def _paperclip_get(path: str) -> dict | list | None:
    """Sync Paperclip API call."""
    try:
        resp = httpx.get(f"{PAPERCLIP_URL}{path}", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _paperclip_patch(path: str, data: dict):
    """Sync Paperclip PATCH call."""
    try:
        resp = httpx.patch(f"{PAPERCLIP_URL}{path}", json=data, timeout=5.0)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def _get_company_stats(company_key: str) -> dict:
    """Fetch stats for a company from Paperclip + evidence DB."""
    company_id = config.get(company_key, {}).get("company_id")
    if not company_id:
        return {"name": company_key, "agents": [], "issues_done": 0, "total_cost": 0}

    company = _paperclip_get(f"/companies/{company_id}") or {}
    agents = _paperclip_get(f"/companies/{company_id}/agents") or []
    issues = _paperclip_get(f"/companies/{company_id}/issues") or []

    active_issues = [i for i in issues if i.get("status") not in ("cancelled",)]
    done_count = sum(1 for i in active_issues if i.get("status") == "done")

    # Cost from evidence DB only (Paperclip's spentMonthlyCents is cumulative and can't be reset)
    ev_summary = get_dashboard_summary(since=_competition_since()).get(company_key, {})
    total_spent = ev_summary.get("total_cost", 0)

    return {
        "name": company.get("name", company_key),
        "company_id": company_id,
        "agent_count": len(agents),
        "agents": [
            {
                "name": a["name"],
                "role": a["role"],
                "status": a["status"],
                "last_heartbeat": a.get("lastHeartbeatAt"),
                "model": a.get("adapterConfig", {}).get("model", "unknown"),
            }
            for a in agents
        ],
        "issues_done": done_count,
        "issues_total": len(active_issues),
        "total_cost_usd": total_spent,
        "budget_usd": company.get("budgetMonthlyCents", 0) / 100.0,
    }


def _fmt_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _get_timer() -> dict:
    """Competition timer."""
    total = _get_timing()["competition_duration_seconds"]

    if not start_time or not competition_active:
        return {"elapsed": "00:00:00", "remaining": _fmt_time(total), "progress_pct": 0, "active": False}

    elapsed = time.time() - start_time
    remaining = max(0, total - elapsed)

    # Freeze timer when competition finishes
    if competition_finished:
        reason = ""
        if competition_winner:
            reason = competition_winner.get("reason", "")
        remaining_label = "COMPLETE" if reason != "timer_expired" else "TIME'S UP"
        return {
            "elapsed": _fmt_time(elapsed),
            "remaining": remaining_label,
            "progress_pct": 100,
            "active": True,
        }

    return {
        "elapsed": _fmt_time(elapsed),
        "remaining": _fmt_time(remaining),
        "progress_pct": min(100, round(elapsed / total * 100, 1)),
        "active": True,
    }


def _get_united_state() -> dict | None:
    """Check if Gladiator United is active and return its state."""
    companies = _paperclip_get("/companies") or []
    united = next((c for c in companies if c.get("name") == "Gladiator United" and c.get("status") == "active"), None)
    if not united:
        return None
    agents = _paperclip_get(f"/companies/{united['id']}/agents") or []
    issues = _paperclip_get(f"/companies/{united['id']}/issues") or []
    return {
        "name": "Gladiator United",
        "company_id": united["id"],
        "agent_count": len(agents),
        "agents": [{"id": a["id"], "name": a["name"], "role": a["role"], "status": a["status"],
                     "last_heartbeat": a.get("lastHeartbeatAt"),
                     "model": a.get("adapterConfig", {}).get("model", "unknown")} for a in agents],
        "issues_done": sum(1 for i in issues if i.get("status") == "done"),
        "issues_total": sum(1 for i in issues if i.get("status") not in ("cancelled",)),
        "issues": [{"title": i["title"], "status": i["status"]} for i in issues],
    }


@app.get("/api/state")
async def get_state():
    """Full dashboard state — polled by frontend."""
    blitz = _get_company_stats("blitz")
    craft = _get_company_stats("craft")
    summary = get_dashboard_summary(since=_competition_since())
    milestones = get_milestones(limit=30)
    timer = _get_timer()

    # Merge evidence into company stats
    for key, stats in [("blitz", blitz), ("craft", craft)]:
        ev = summary.get(key, {})
        stats["skills_count"] = ev.get("total_skills", 0)
        stats["skill_versions"] = ev.get("skill_versions", 0)
        stats["total_heartbeats"] = ev.get("total_heartbeats", 0)
        stats["milestones_count"] = ev.get("total_milestones", 0)
        stats["stars"] = _simulate_stars(stats)

    # Auto-complete: stop competition when all tasks done OR timer expires
    global competition_finished, competition_winner
    b_done = blitz.get("issues_done", 0)
    c_done = craft.get("issues_done", 0)
    b_total = blitz.get("issues_total", 0)
    c_total = craft.get("issues_total", 0)

    # Condition 1: all tasks done
    all_tasks_done = (b_total > 0 and c_total > 0
                      and b_done >= b_total and c_done >= c_total)
    # Condition 2: timer expired (failsafe — competition must end even if agents stall)
    timer_expired = False
    if start_time:
        elapsed_secs = time.time() - start_time
        total_secs = _get_timing()["competition_duration_seconds"]
        timer_expired = elapsed_secs >= total_secs

    if (competition_active and not competition_finished
            and (all_tasks_done or timer_expired)):
        competition_finished = True
        b_stars = blitz.get("stars", 0)
        c_stars = craft.get("stars", 0)
        if b_stars > c_stars:
            winner_name, winner_stars, loser_stars = "Blitz", b_stars, c_stars
        elif c_stars > b_stars:
            winner_name, winner_stars, loser_stars = "Craft", c_stars, b_stars
        else:
            winner_name, winner_stars, loser_stars = "Tie", b_stars, c_stars
        competition_winner = {
            "winner": winner_name,
            "winner_stars": winner_stars,
            "loser_stars": loser_stars,
            "elapsed": timer.get("elapsed", ""),
            "blitz_stars": b_stars,
            "craft_stars": c_stars,
            "reason": "all_tasks_done" if all_tasks_done else "timer_expired",
        }
        # Pause all agents to stop burning tokens
        for ck in ("blitz", "craft"):
            cid = config.get(ck, {}).get("company_id")
            if cid:
                agents_list = _paperclip_get(f"/companies/{cid}/agents") or []
                for a in agents_list:
                    _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})

    # Check for merged state
    united = _get_united_state()

    # Auto-pause united agents when post-merge task is done
    if united and united.get("issues_total", 0) > 0 and united["issues_done"] >= united["issues_total"]:
        for a in united.get("agents", []):
            if a.get("status") not in ("paused",):
                _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})

    # Capture intel snapshot if new heartbeats detected
    if competition_active:
        global last_known_hb_count
        since_sql = f"AND created_at >= '{_competition_since()}'" if _competition_since() else ""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        current_hb = conn.execute(f"SELECT COUNT(*) FROM heartbeat_metrics WHERE 1=1 {since_sql}").fetchone()[0]
        conn.close()
        if current_hb > last_known_hb_count:
            last_known_hb_count = current_hb
            snap_checks, snap_insights, snap_summary = _compute_checks_and_insights(blitz, craft)
            intel_history.append({
                "snapshot": len(intel_history) + 1,
                "heartbeat_total": current_hb,
                "elapsed": timer.get("elapsed", "00:00:00"),
                "checks": snap_checks,
                "insights": snap_insights,
                "summary": snap_summary,
            })
            # Cap intel history to prevent memory growth
            if len(intel_history) > 100:
                intel_history[:] = intel_history[-100:]

    result = {
        "timer": timer,
        "blitz": blitz,
        "craft": craft,
        "milestones": milestones,
    }
    if competition_finished and competition_winner:
        result["finished"] = True
        result["winner"] = competition_winner
    if united:
        result["united"] = united
        result["merged"] = True
    return result


def _simulate_stars(stats: dict) -> int:
    """Simulate star count based on meaningful work (not just heartbeats)."""
    base = 0
    base += stats.get("issues_done", 0) * 8
    base += stats.get("skills_count", 0) * 5
    base += stats.get("skill_versions", 0) * 3
    # Heartbeats alone don't generate stars — only real work counts
    return base


@app.get("/api/learning")
async def get_learning():
    """Full learning evidence report."""
    return generate_learning_report()


@app.get("/api/skills/{company}")
async def get_skills(company: str):
    """Skill timeline for a company."""
    return get_skill_timeline(company=company)


@app.get("/api/efficiency/{company}")
async def get_efficiency(company: str):
    """Token efficiency trend for a company."""
    return compute_efficiency_trend(company)


@app.get("/api/memory/{agent_id}")
async def get_memory(agent_id: str):
    """Memory growth for an agent."""
    return get_memory_growth(agent_id=agent_id)


@app.get("/api/memory-by-company/{company}")
async def get_memory_by_company(company: str):
    """Memory growth for all agents in a company."""
    all_memory = get_memory_growth()
    return [m for m in all_memory if m.get("company") == company]


def _extract_run_summary(lines: list[str]) -> dict:
    """Extract task title and action summary from run log lines."""
    import re
    task_title = ""
    actions = []

    for line_str in lines:
        try:
            d = json.loads(line_str)
        except Exception:
            continue
        chunk = d.get("chunk", "")

        # Find task title
        if not task_title:
            match = re.search(r"\*\*(.+?)\*\*", chunk)
            if match and len(match.group(1)) > 5:
                task_title = match.group(1)

        # Extract agent thought lines (💬 = agent speaking)
        for part in chunk.split("\n"):
            part = part.strip()
            if "\U0001f4ac" in part:  # 💬
                # Clean up the thought line
                thought = re.sub(r"^.*\U0001f4ac\s*", "", part).strip()
                if thought and len(thought) > 10:
                    actions.append(thought[:150])

    # Pick the most descriptive action as summary
    summary = ""
    for action in actions:
        if any(kw in action.lower() for kw in ["let me", "i'll", "perfect", "found", "created", "added", "wrote", "shipped", "drafted", "fixed", "updated"]):
            summary = action
            break
    if not summary and actions:
        summary = actions[0]

    return {"task_title": task_title or "Heartbeat", "summary": summary}


@app.get("/api/gantt")
async def get_gantt():
    """Activity timeline data for Gantt chart — built from Paperclip run logs."""
    if not competition_active:
        return []

    # Only show runs that started after competition launch
    cutoff = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat() if start_time else None

    activity = []
    for key in ("blitz", "craft"):
        company_id = config.get(key, {}).get("company_id")
        if not company_id:
            continue
        agents = _paperclip_get(f"/companies/{company_id}/agents") or []

        for agent in agents:
            agent_id = agent["id"]
            log_dir = Path.home() / ".paperclip" / "instances" / "default" / "data" / "run-logs" / company_id / agent_id
            if not log_dir.exists():
                continue

            for log_file in sorted(log_dir.glob("*.ndjson")):
                run_id = log_file.stem
                try:
                    lines = log_file.read_text().strip().split("\n")
                    if not lines:
                        continue
                    first = json.loads(lines[0])
                    last = json.loads(lines[-1])
                    run_start = first.get("ts")
                    end_time = last.get("ts")

                    if cutoff and run_start and run_start < cutoff:
                        continue

                    info = _extract_run_summary(lines)

                    activity.append({
                        "agent_name": agent["name"],
                        "company": key,
                        "run_id": run_id,
                        "start_time": run_start,
                        "end_time": end_time,
                        "task_title": info["task_title"],
                        "summary": info["summary"],
                        "status": "completed",
                    })
                except Exception:
                    continue

    return activity


@app.get("/api/audit")
async def get_audit():
    """Side-by-side audit trail showing what each company's agents decided/did."""
    audit = {"blitz": [], "craft": []}

    if not competition_active:
        return audit

    # Only show events after competition start
    cutoff_iso = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat() if start_time else None

    for key in ("blitz", "craft"):
        company_id = config.get(key, {}).get("company_id")
        if not company_id:
            continue
        agents = _paperclip_get(f"/companies/{company_id}/agents") or []
        issues = _paperclip_get(f"/companies/{company_id}/issues") or []

        # Completed issues with details (only those completed after start)
        for issue in issues:
            if issue.get("status") == "done":
                updated = issue.get("updatedAt", "")
                if cutoff_iso and updated and updated < cutoff_iso:
                    continue
                assignee = issue.get("assigneeAgentId", "")
                agent_name = ""
                for a in agents:
                    if a["id"] == assignee:
                        agent_name = a["name"]
                        break

                audit[key].append({
                    "type": "task_completed",
                    "agent": agent_name or "Unknown",
                    "title": issue["title"],
                    "timestamp": updated,
                })

        # Git commits from the repo (only after competition start)
        import subprocess
        repo_dir = "company_a/repo" if key == "blitz" else "company_b/repo"
        repo_path = Path(__file__).parent.parent / repo_dir
        if repo_path.exists() and start_time:
            try:
                after_date = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
                result = subprocess.run(
                    ["git", "log", "--oneline", "--format=%H|%s|%ai", f"--after={after_date}"],
                    cwd=str(repo_path), capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.strip().split("\n"):
                    if "|" not in line:
                        continue
                    parts = line.split("|", 2)
                    if len(parts) >= 3:
                        audit[key].append({
                            "type": "commit",
                            "agent": "Engineer",
                            "title": parts[1],
                            "timestamp": parts[2].strip(),
                        })
            except Exception:
                pass

        audit[key].sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return audit


@app.get("/api/sessions")
async def get_sessions():
    """Session chains for all agents."""
    return get_session_chains()


@app.get("/api/activity")
async def get_activity():
    """Recent activity from Paperclip."""
    activity = []
    for key in ("blitz", "craft"):
        company_id = config.get(key, {}).get("company_id")
        if not company_id:
            continue
        agents = _paperclip_get(f"/companies/{company_id}/agents") or []
        for agent in agents:
            if agent.get("lastHeartbeatAt"):
                activity.append({
                    "company": key,
                    "agent": agent["name"],
                    "role": agent["role"],
                    "status": agent["status"],
                    "timestamp": agent["lastHeartbeatAt"],
                })
    activity.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return activity[:20]


@app.get("/api/taskboard")
async def get_taskboard():
    """Returns Paperclip issues grouped by status for both companies."""
    boards = {}
    for key in ("blitz", "craft"):
        company_id = config.get(key, {}).get("company_id")
        if not company_id:
            continue
        issues = _paperclip_get(f"/companies/{company_id}/issues") or []
        boards[key] = {
            "todo": [i for i in issues if i.get("status") in ("backlog", "todo")],
            "in_progress": [i for i in issues if i.get("status") == "in_progress"],
            "done": [i for i in issues if i.get("status") == "done"],
        }
    return boards


def _compute_checks_and_insights(blitz_stats=None, craft_stats=None):
    """Compute checks + insights from current state. Returns (checks, insights, summary)."""
    blitz = blitz_stats or _get_company_stats("blitz")
    craft = craft_stats or _get_company_stats("craft")
    summary_data = get_dashboard_summary(since=_competition_since())
    since = _competition_since()
    since_sql = f"AND created_at >= '{since}'" if since else ""

    for key, stats in [("blitz", blitz), ("craft", craft)]:
        ev = summary_data.get(key, {})
        stats.setdefault("skills_count", ev.get("total_skills", 0))
        stats.setdefault("skill_versions", ev.get("skill_versions", 0))
        stats.setdefault("total_heartbeats", ev.get("total_heartbeats", 0))

    checks = []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    for key, stats in [("blitz", blitz), ("craft", craft)]:
        company_id = config.get(key, {}).get("company_id")
        if not company_id:
            continue
        issues = _paperclip_get(f"/companies/{company_id}/issues") or []
        active_issues = [i for i in issues if i.get("status") not in ("cancelled",)]
        done = sum(1 for i in active_issues if i.get("status") == "done")
        checks.append({"name": f"{key}_tasks", "status": "pass", "detail": f"{done}/{len(active_issues)} done"})
        db_cost = conn.execute(f"SELECT COALESCE(SUM(cost_usd),0) FROM heartbeat_metrics WHERE company=? {since_sql}", (key,)).fetchone()[0]
        checks.append({"name": f"{key}_cost", "status": "pass", "detail": f"${db_cost:.3f}"})
        skill_count = summary_data.get(key, {}).get("total_skills", 0)
        usage_count = conn.execute(f"SELECT COUNT(*) FROM skill_usage_events WHERE company=? {since_sql}", (key,)).fetchone()[0]
        checks.append({"name": f"{key}_skills", "status": "pass", "detail": f"{skill_count} created, {usage_count} used"})

    insights = []
    b_done = blitz.get("issues_done", 0)
    c_done = craft.get("issues_done", 0)
    b_hb = max(blitz.get("total_heartbeats", 0), 1)
    c_hb = max(craft.get("total_heartbeats", 0), 1)

    if b_done > 0 or c_done > 0:
        b_rate = round(b_done / b_hb, 2)
        c_rate = round(c_done / c_hb, 2)
        faster = "Blitz" if b_rate > c_rate else "Craft" if c_rate > b_rate else "Tied"
        insights.append({"type": "pace", "text": f"{faster} completing tasks faster ({b_rate} vs {c_rate} tasks/heartbeat)"})
        b_cost = blitz.get("total_cost_usd", 0)
        c_cost = craft.get("total_cost_usd", 0)
        insights.append({"type": "pace", "text": f"Cost per task: Blitz ${round(b_cost/max(b_done,1),3)} vs Craft ${round(c_cost/max(c_done,1),3)}"})

    b_skills = blitz.get("skills_count", 0)
    c_skills = craft.get("skills_count", 0)
    if b_skills > 0 or c_skills > 0:
        insights.append({"type": "learning", "text": f"Skills: Blitz {b_skills} ({round(b_skills/b_hb,2)}/hb) vs Craft {c_skills} ({round(c_skills/c_hb,2)}/hb)"})

    for key in ("blitz", "craft"):
        usage_cnt = conn.execute(f"SELECT COUNT(*) FROM skill_usage_events WHERE company=? {since_sql}", (key,)).fetchone()[0]
        if usage_cnt > 0:
            insights.append({"type": "learning", "text": f"{key.title()} agents used skills {usage_cnt} time(s) — Hermes learning loop active"})
        max_mem = conn.execute(f"SELECT MAX(char_count) FROM memory_snapshots WHERE company=? AND memory_type='memory' {since_sql}", (key,)).fetchone()[0] or 0
        if max_mem > 0:
            insights.append({"type": "learning", "text": f"{key.title()} peak memory: {max_mem} chars"})

    b_stars = _simulate_stars(blitz)
    c_stars = _simulate_stars(craft)
    leader = "Blitz" if b_stars > c_stars else "Craft" if c_stars > b_stars else "Tied"
    if b_stars > 0 or c_stars > 0:
        insights.append({"type": "strategy", "text": f"{leader} leads {max(b_stars,c_stars)} to {min(b_stars,c_stars)} projected stars"})

    timer = _get_timer()
    summary = f"{leader} leads {b_stars}-{c_stars}. {b_done+c_done} tasks done. {timer['remaining']} remaining."
    conn.close()
    return checks, insights, summary


@app.get("/api/insights")
async def get_insights():
    """Cross-system consistency checks and strategic insights."""
    if not competition_active:
        return {"checks": [], "insights": [], "summary": "No active competition"}

    checks, insights, summary = _compute_checks_and_insights()

    # Also add agent liveness + united + divergence checks (extended, only on intel page)
    since = _competition_since()
    since_sql = f"AND created_at >= '{since}'" if since else ""

    # Agent liveness from error logs
    for key in ("blitz", "craft"):
        agents_cfg = config.get(key, {}).get("agents", {})
        hermes_base = Path.home() / ".hermes" / "gladiator"
        agent_errors = []
        for agent_key in agents_cfg:
            err_log = hermes_base / agent_key / "logs" / "errors.log"
            if err_log.exists():
                recent_errors = err_log.read_text().strip().split("\n")[-3:]
                for line in recent_errors:
                    if "credit balance is too low" in line:
                        agent_errors.append(f"{agent_key}: API credits exhausted")
                        break
                    elif "ERROR" in line and since and line[:19] >= since:
                        agent_errors.append(f"{agent_key}: {line.split('ERROR')[-1][:60].strip()}")
                        break
        if agent_errors:
            checks.append({"name": f"{key}_agents", "status": "fail", "detail": agent_errors[0]})
        else:
            company_id = config.get(key, {}).get("company_id")
            agents = _paperclip_get(f"/companies/{company_id}/agents") or [] if company_id else []
            checks.append({"name": f"{key}_agents", "status": "pass", "detail": f"{len(agents)} agents healthy"})

    # United company check
    united = _get_united_state()
    if united:
        running = sum(1 for a in united["agents"] if a["status"] == "running")
        checks.append({"name": "united", "status": "pass" if running > 0 else "warn",
                       "detail": f"{united['agent_count']} agents, {running} running, {united['issues_done']}/{united['issues_total']} tasks"})
        task_status = "complete" if united["issues_done"] >= united["issues_total"] else "in progress"
        insights.append({"type": "strategy", "text": f"MERGED: Gladiator United — {united['agent_count']} agents, post-merge task {task_status}"})
        if united["issues_done"] >= united["issues_total"]:
            insights.append({"type": "learning", "text": "Cross-company skill transfer PROVEN — Blitz engineer reviewed Craft's merged skills"})

    # Code divergence
    if start_time:
        import subprocess
        for key, repo_dir in [("blitz", "company_a/repo"), ("craft", "company_b/repo")]:
            repo_path = Path(__file__).parent.parent / repo_dir
            if repo_path.exists():
                try:
                    after = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
                    r = subprocess.run(["git", "rev-list", "--count", f"--after={after}", "HEAD"],
                                       cwd=str(repo_path), capture_output=True, text=True, timeout=5)
                    cc = int(r.stdout.strip()) if r.returncode == 0 else 0
                    if cc > 0:
                        insights.append({"type": "divergence", "text": f"{key.title()} repo: {cc} new commit(s) since start"})
                except Exception:
                    pass

    timer = _get_timer()
    merged_suffix = f" [MERGED] Post-merge: {task_status}." if united else ""
    summary = f"{summary}{merged_suffix}" if united else summary

    # Snapshot if new heartbeats (for when intel page is viewed directly)
    global last_known_hb_count
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    current_hb_count = conn.execute(f"SELECT COUNT(*) FROM heartbeat_metrics WHERE 1=1 {since_sql}").fetchone()[0]
    conn.close()
    if current_hb_count > last_known_hb_count:
        last_known_hb_count = current_hb_count
        snapshot_num = len(intel_history) + 1
        intel_history.append({
            "snapshot": snapshot_num,
            "heartbeat_total": current_hb_count,
            "elapsed": timer.get("elapsed", "00:00:00"),
            "checks": checks,
            "insights": insights,
            "summary": summary,
        })

    return {"current": {"checks": checks, "insights": insights, "summary": summary},
            "history": intel_history}


@app.get("/api/code-comparison")
async def get_code_comparison():
    """Returns git stats for both company repos."""
    import subprocess

    if not competition_active:
        return {"blitz": {"commits": 0, "files": 0, "categories": {}, "recent_commits": [], "file_tree": []},
                "craft": {"commits": 0, "files": 0, "categories": {}, "recent_commits": [], "file_tree": []}}

    after_date = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S") if start_time else None

    stats = {}
    for key, repo_dir in [("blitz", "company_a/repo"), ("craft", "company_b/repo")]:
        repo_path = Path(__file__).parent.parent / repo_dir
        if not repo_path.exists():
            continue
        try:
            # Commit count (after competition start)
            if after_date:
                commits = subprocess.run(
                    ["git", "rev-list", "--count", f"--after={after_date}", "HEAD"],
                    cwd=str(repo_path), capture_output=True, text=True, timeout=5,
                )
            else:
                commits = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    cwd=str(repo_path), capture_output=True, text=True, timeout=5,
                )
            # File stats: if no new commits, show initial state; otherwise show current
            commit_count = int(commits.stdout.strip()) if commits.returncode == 0 else 0
            if commit_count == 0:
                # Show files at initial commit (both repos start identical)
                files = subprocess.run(
                    ["git", "ls-files"],
                    cwd=str(repo_path), capture_output=True, text=True, timeout=5,
                )
            else:
                files = subprocess.run(
                    ["git", "ls-files"],
                    cwd=str(repo_path), capture_output=True, text=True, timeout=5,
                )
            file_list = [
                f for f in files.stdout.strip().split("\n")
                if f and not f.startswith(".") and not f.startswith("venv/") and ".venv/" not in f
                and "__pycache__" not in f and ".pyc" not in f
            ]
            # Categorize files
            categories = {"features": 0, "tests": 0, "docs": 0, "config": 0}
            for f in file_list:
                if "test" in f.lower():
                    categories["tests"] += 1
                elif f.endswith(".md") or f.endswith(".rst") or f.endswith(".txt"):
                    categories["docs"] += 1
                elif f.endswith(".py") or f.endswith(".js"):
                    categories["features"] += 1
                else:
                    categories["config"] += 1
            # Recent commits (after competition start)
            log_args = ["git", "log", "--oneline", "-5"]
            if after_date:
                log_args.append(f"--after={after_date}")
            log = subprocess.run(
                log_args,
                cwd=str(repo_path), capture_output=True, text=True, timeout=5,
            )
            stats[key] = {
                "commits": commit_count,
                "files": len(file_list),
                "categories": categories,
                "recent_commits": [l.strip() for l in log.stdout.strip().split("\n") if l.strip()],
                "file_tree": file_list[:30],
            }
        except Exception as e:
            stats[key] = {"error": str(e)}
    return stats


async def event_generator(request: Request):
    """SSE stream — pushes state updates every 5 seconds."""
    while True:
        if await request.is_disconnected():
            break
        try:
            state = await get_state()
            yield {"event": "state", "data": json.dumps(state)}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
        await asyncio.sleep(5)


@app.get("/api/stream")
async def stream(request: Request):
    """SSE endpoint for live updates."""
    return EventSourceResponse(event_generator(request))


@app.get("/landing")
async def landing_page():
    """Serve the landing/splash screen."""
    landing_path = Path(__file__).parent / "static" / "landing.html"
    return HTMLResponse(landing_path.read_text())


@app.get("/api/agent-details")
async def get_agent_details():
    """Per-agent breakdown: deliverables, skills, memory, heartbeats."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    details = {}
    for company_key in ("blitz", "craft"):
        company_id = config.get(company_key, {}).get("company_id")
        if not company_id:
            continue
        agents = _paperclip_get(f"/companies/{company_id}/agents") or []
        issues = _paperclip_get(f"/companies/{company_id}/issues") or []

        for agent in agents:
            agent_id = agent["id"]
            # Find agent_key
            agent_key = None
            for key, aid in config.get(company_key, {}).get("agents", {}).items():
                if aid == agent_id:
                    agent_key = key
                    break
            if not agent_key:
                continue

            # Tasks assigned to this agent (exclude cancelled)
            agent_issues = [i for i in issues if i.get("assigneeAgentId") == agent_id and i.get("status") not in ("cancelled",)]
            all_tasks = [{"title": i["title"], "status": i["status"]} for i in agent_issues]
            done_count = sum(1 for i in agent_issues if i.get("status") == "done")

            # Filter evidence by competition start
            since = _competition_since()
            since_sql = f"AND created_at >= '{since}'" if since else ""

            # Skills
            skills = [dict(r) for r in conn.execute(
                f"SELECT skill_name, version, created_at FROM skill_snapshots WHERE agent_id=? {since_sql} ORDER BY created_at",
                (agent_key,)
            ).fetchall()]

            # Memory
            memory_rows = conn.execute(
                f"SELECT char_count, heartbeat_num, created_at FROM memory_snapshots "
                f"WHERE agent_id=? AND memory_type='memory' AND char_count > 0 {since_sql} ORDER BY heartbeat_num",
                (agent_key,)
            ).fetchall()
            memory_progression = [{"chars": r["char_count"], "heartbeat": r["heartbeat_num"]} for r in memory_rows]

            # Heartbeat count + cost
            hb_rows = conn.execute(
                f"SELECT COUNT(*) as cnt, SUM(cost_usd) as total_cost, SUM(tokens_in+tokens_out) as total_tokens "
                f"FROM heartbeat_metrics WHERE agent_id=? {since_sql}", (agent_key,)
            ).fetchone()

            # Milestones
            milestones = [dict(r) for r in conn.execute(
                f"SELECT milestone_type, description, created_at FROM learning_milestones WHERE agent_id=? {since_sql} ORDER BY created_at",
                (agent_key,)
            ).fetchall()]

            details[agent_key] = {
                "name": agent["name"],
                "role": agent["role"],
                "company": company_key,
                "model": agent.get("adapterConfig", {}).get("model", "unknown"),
                "status": agent["status"],
                "tasks": all_tasks,
                "tasks_done": done_count,
                "skills": skills,
                "memory_progression": memory_progression,
                "heartbeats": hb_rows["cnt"] if hb_rows else 0,
                "total_cost": round(hb_rows["total_cost"] or 0, 4) if hb_rows else 0,
                "total_tokens": hb_rows["total_tokens"] or 0 if hb_rows else 0,
                "milestones": milestones,
            }

    conn.close()
    return details


@app.get("/api/memory-content/{agent_key}")
async def get_memory_content(agent_key: str):
    """Return the actual MEMORY.md content for an agent."""
    mem_path = Path.home() / ".hermes" / "gladiator" / agent_key / "memories" / "MEMORY.md"
    if mem_path.exists():
        content = mem_path.read_text().strip()
        # Split by section separator
        sections = [s.strip() for s in content.split("§") if s.strip()]
        return {"agent": agent_key, "content": content, "sections": sections, "chars": len(content)}
    return {"agent": agent_key, "content": "", "sections": [], "chars": 0}


@app.get("/comparison")
async def comparison_page():
    """Serve the repo comparison page."""
    comparison_path = Path(__file__).parent / "static" / "comparison.html"
    return HTMLResponse(comparison_path.read_text())


@app.get("/intel")
async def intel_page():
    """Serve the intel report page."""
    intel_path = Path(__file__).parent / "static" / "intel.html"
    return HTMLResponse(intel_path.read_text())


@app.post("/api/merge")
async def do_merge():
    """Merge Blitz + Craft into Gladiator United, then trigger cross-company task."""
    import subprocess, shutil

    # Guard: prevent duplicate merges
    companies = _paperclip_get("/companies") or []
    existing_united = [c for c in companies if c.get("name") == "Gladiator United" and c.get("status") == "active"]
    if existing_united:
        return {"status": "already_merged", "output": "Gladiator United already exists.",
                "post_merge": "Merge was already completed."}

    # Step 1: Run merge script
    result = subprocess.run(
        [str(Path(__file__).parent.parent / "base-product" / ".venv" / "bin" / "python"),
         str(Path(__file__).parent.parent / "scripts" / "merge_companies.py")],
        capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).parent.parent),
    )
    if result.returncode != 0:
        return {"status": "error", "output": result.stdout[-500:], "error": result.stderr[-500:]}

    # Step 2: Copy merged skills into each agent's HERMES_HOME so they can access rival skills
    hermes_base = Path.home() / ".hermes" / "gladiator"
    united_skills = hermes_base / "united" / "skills"
    if united_skills.exists():
        for company_key in ("blitz", "craft"):
            for agent_key in config.get(company_key, {}).get("agents", {}).keys():
                agent_skills = hermes_base / agent_key / "skills"
                agent_skills.mkdir(parents=True, exist_ok=True)
                for skill_dir in united_skills.iterdir():
                    if skill_dir.is_dir():
                        dest = agent_skills / skill_dir.name
                        if not dest.exists():
                            shutil.copytree(str(skill_dir), str(dest))

    # Step 3: Find the United company and trigger a cross-company skill transfer task
    post_merge_info = ""
    try:
        companies = _paperclip_get("/companies") or []
        united = next((c for c in companies if c.get("name") == "Gladiator United" and c.get("status") == "active"), None)
        if united:
            united_agents = _paperclip_get(f"/companies/{united['id']}/agents") or []
            # Find a former Blitz engineer
            blitz_eng = next((a for a in united_agents if "Blitz" in a.get("name", "") and "Engineer" in a.get("name", "")), None)
            if blitz_eng:
                # Create a task that requires using Craft's skills
                issue = httpx.post(
                    f"{PAPERCLIP_URL}/companies/{united['id']}/issues",
                    json={
                        "title": "Post-Merge: Review merged skill library and save summary",
                        "description": "You are now part of Gladiator United. Both Blitz and Craft teams have merged their skills into your skill library. List the available skills using skill_list, pick one from the rival team, load it with skill_view, and save a brief summary of what you learned to memory. Keep it short.",
                        "status": "todo",
                        "priority": "high",
                        "assigneeAgentId": blitz_eng["id"],
                    },
                    timeout=5.0,
                ).json()
                # Set short timeout for merge task and trigger
                _paperclip_patch(f"/agents/{blitz_eng['id']}", {
                    "adapterConfig": {**blitz_eng.get("adapterConfig", {}), "timeoutSec": 60, "maxIterations": 10},
                })
                httpx.post(
                    f"{PAPERCLIP_URL}/agents/{blitz_eng['id']}/wakeup",
                    json={"source": "on_demand", "reason": "Post-merge cross-company skill transfer"},
                    timeout=5.0,
                )
                post_merge_info = f"Post-merge task assigned to {blitz_eng['name']}: reviewing merged skill library"
    except Exception as e:
        post_merge_info = f"Post-merge task failed: {e}"

    return {
        "status": "merged",
        "output": result.stdout[-500:],
        "post_merge": post_merge_info,
    }


@app.post("/api/unmerge")
async def do_unmerge():
    """Restore Blitz + Craft, archive United, delete duplicate agents."""
    cfg = json.loads(CONFIG_PATH.read_text())
    blitz_id = cfg["blitz"]["company_id"]
    craft_id = cfg["craft"]["company_id"]

    # Restore originals
    _paperclip_patch(f"/companies/{blitz_id}", {"status": "active"})
    _paperclip_patch(f"/companies/{craft_id}", {"status": "active"})

    # Find and archive any "Gladiator United" companies + delete their duplicate agents
    companies = _paperclip_get("/companies") or []
    deleted_agents = 0
    for c in companies:
        if c.get("name") == "Gladiator United" and c.get("status") == "active":
            # Delete duplicate agents created by merge
            united_agents = _paperclip_get(f"/companies/{c['id']}/agents") or []
            for a in united_agents:
                try:
                    httpx.delete(f"{PAPERCLIP_URL}/agents/{a['id']}", timeout=5.0)
                    deleted_agents += 1
                except Exception:
                    pass
            _paperclip_patch(f"/companies/{c['id']}", {"status": "archived"})

    return {"status": "unmerged", "deleted_agents": deleted_agents}


@app.post("/api/stop-demo")
async def stop_demo():
    """Stop competition, pause all agents, redirect to landing."""
    global competition_active, competition_finished
    competition_active = False
    competition_finished = True

    # Pause all agents
    for company_key in ("blitz", "craft"):
        company_id = config.get(company_key, {}).get("company_id")
        if company_id:
            agents = _paperclip_get(f"/companies/{company_id}/agents") or []
            for a in agents:
                _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})
    companies = _paperclip_get("/companies") or []
    for c in companies:
        if c.get("name") == "Gladiator United" and c.get("status") == "active":
            agents = _paperclip_get(f"/companies/{c['id']}/agents") or []
            for a in agents:
                _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})

    # Kill hermes processes
    import subprocess
    subprocess.run(["pkill", "-9", "-f", "hermes chat"], capture_output=True)

    return {"status": "stopped", "message": "Competition stopped. All agents paused."}


@app.post("/api/reset-demo")
async def reset_demo():
    """True reset: wipe evidence, reset tasks, reset timer, restore companies."""
    import shutil
    global start_time, competition_active, competition_finished, competition_winner, intel_history, last_known_hb_count
    intel_history = []
    last_known_hb_count = 0
    competition_finished = False
    competition_winner = None
    competition_active = False

    # 0. Pause ALL agents immediately to stop burning tokens
    for company_key in ("blitz", "craft"):
        company_id = config.get(company_key, {}).get("company_id")
        if company_id:
            agents = _paperclip_get(f"/companies/{company_id}/agents") or []
            for a in agents:
                _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})
    # Also pause united agents
    companies = _paperclip_get("/companies") or []
    for c in companies:
        if c.get("name") == "Gladiator United" and c.get("status") == "active":
            agents = _paperclip_get(f"/companies/{c['id']}/agents") or []
            for a in agents:
                _paperclip_patch(f"/agents/{a['id']}", {"status": "paused"})

    # 1. Backup evidence.db
    db_path = Path(__file__).parent.parent / "evidence.db"
    backup_path = Path(__file__).parent.parent / "evidence.db.backup"
    if db_path.exists():
        shutil.copy2(str(db_path), str(backup_path))

    # 2. Delete ALL existing issues, recreate hardcoded Round 1 tasks
    round1_tasks = {
        "blitz": [
            # CEO: sets direction, delegates
            {"title": "Define growth strategy and assign team priorities",
             "description": "You're the CEO. Analyze the llm-judge codebase at /home/exitcode42/python_projects/gladiator/company_a/repo. Define the top 3 features that will drive GitHub stars. Your team: Engineer (builds features), CMO (marketing), Content (blog/changelog). Save your strategy to memory.", "role": "ceo"},
            # Engineer: builds features
            {"title": "Add --speed-test mode with live racing progress bars",
             "description": "Create a speed-test mode that races models against each other with Rich progress bars and a leaderboard. This is the hero feature — make it screenshot-worthy. Code at /home/exitcode42/python_projects/gladiator/company_a/repo.", "role": "engineer"},
            # Engineer: second feature
            {"title": "Add colorful ASCII banner and --json output flag",
             "description": "Add a colorful ASCII art banner on CLI startup (Rich library) and a --json flag for programmatic output. Two quick wins. Code at /home/exitcode42/python_projects/gladiator/company_a/repo.", "role": "engineer"},
            # CMO: marketing
            {"title": "Draft launch tweets and Show HN post",
             "description": "Write 3 engaging tweets promoting llm-judge's speed-test feature. Draft a Show HN post. Focus on the racing visual — developers love terminal eye candy.", "role": "cmo"},
            # Content: docs
            {"title": "Write CHANGELOG.md and product blog post",
             "description": "Write a changelog covering all shipped features and a blog post announcing llm-judge. Make it engaging — this content drives traffic to the repo.", "role": "general"},
        ],
        "craft": [
            # CEO: sets direction, delegates
            {"title": "Define product quality strategy and assign team priorities",
             "description": "You're the CEO. Analyze the llm-judge codebase at /home/exitcode42/python_projects/gladiator/company_b/repo. Define the quality bar: what makes this tool worth starring? Your team: CTO (architecture), Engineer 1 (features), Engineer 2 (testing), Docs (documentation). Save your strategy to memory.", "role": "ceo"},
            # CTO: architecture
            {"title": "Review codebase and create ARCHITECTURE.md",
             "description": "Review the llm-judge codebase. Document the architecture with diagrams. Identify improvement areas. Set coding standards for the team. Code at /home/exitcode42/python_projects/gladiator/company_b/repo.", "role": "cto"},
            # Engineer 1: core feature
            {"title": "Add response quality scoring with configurable criteria",
             "description": "Build a scoring system that evaluates LLM responses on relevance, coherence, completeness. This differentiates us from competitors. Code at /home/exitcode42/python_projects/gladiator/company_b/repo.", "role": "engineer"},
            # Engineer 2: testing
            {"title": "Add unit tests for the scoring module with pytest",
             "description": "Write comprehensive pytest tests for the scoring module. Target 90%+ coverage. Follow best practices — this proves our quality commitment. Code at /home/exitcode42/python_projects/gladiator/company_b/repo.", "role": "qa"},
            # Docs: documentation
            {"title": "Write comprehensive README with examples and quick-start guide",
             "description": "Write a README that converts visitors to stars. Include quick-start, examples, badges, and screenshots. A great README is our best marketing. Code at /home/exitcode42/python_projects/gladiator/company_b/repo.", "role": "general"},
        ],
    }
    for company_key in ("blitz", "craft"):
        company_id = config.get(company_key, {}).get("company_id")
        if not company_id:
            continue
        # Cancel then delete all existing issues (cancel first in case delete fails)
        issues = _paperclip_get(f"/companies/{company_id}/issues") or []
        for issue in issues:
            try:
                _paperclip_patch(f"/issues/{issue['id']}", {"status": "cancelled"})
                httpx.delete(f"{PAPERCLIP_URL}/issues/{issue['id']}", timeout=5.0)
            except Exception:
                pass
        # Recreate agents if they don't exist (e.g. after termination from Paperclip UI)
        agents = _paperclip_get(f"/companies/{company_id}/agents") or []
        if not agents:
            hermes_base = Path.home() / ".hermes" / "gladiator"
            agents_def = {
                "blitz": [
                    {"name": "Blitz CEO", "role": "ceo", "hermes_id": "blitz-ceo", "reports_to": None},
                    {"name": "Blitz CMO", "role": "cmo", "hermes_id": "blitz-cmo", "reports_to": "ceo"},
                    {"name": "Blitz Content", "role": "general", "hermes_id": "blitz-content", "reports_to": "cmo"},
                    {"name": "Blitz Engineer", "role": "engineer", "hermes_id": "blitz-engineer", "reports_to": "ceo"},
                ],
                "craft": [
                    {"name": "Craft CEO", "role": "ceo", "hermes_id": "craft-ceo", "reports_to": None},
                    {"name": "Craft CTO", "role": "cto", "hermes_id": "craft-cto", "reports_to": "ceo"},
                    {"name": "Craft Engineer 1", "role": "engineer", "hermes_id": "craft-eng1", "reports_to": "cto"},
                    {"name": "Craft Engineer 2", "role": "qa", "hermes_id": "craft-eng2", "reports_to": "cto"},
                    {"name": "Craft Docs", "role": "general", "hermes_id": "craft-docs", "reports_to": "ceo"},
                ],
            }
            new_agent_ids = {}
            created_by_role = {}
            for adef in agents_def.get(company_key, []):
                try:
                    role = adef["role"]
                    toolsets = ROLE_TOOLSETS.get(role, "terminal,file,web,skills,memory").split(",")
                    max_iter = ROLE_ITERATIONS.get(role, 30)
                    timeout_s = ROLE_TIMEOUT.get(role, 600)
                    agent_json = {
                        "name": adef["name"], "role": role,
                        "adapterType": "hermes_local",
                        "adapterConfig": {
                            "model": "claude-sonnet-4-20250514", "provider": "anthropic",
                            "maxIterations": max_iter, "timeoutSec": timeout_s, "persistSession": False,
                            "enabledToolsets": toolsets,
                            "env": {"HERMES_HOME": str(hermes_base / adef["hermes_id"])},
                            "cwd": str(Path(__file__).parent.parent / ("company_a/repo" if company_key == "blitz" else "company_b/repo")),
                        },
                        "heartbeatIntervalSeconds": timing.get("heartbeat_engineer_seconds", 150) if role in ("ceo","cto","engineer","qa") else timing.get("heartbeat_support_seconds", 300),
                        "budgetMonthlyCents": 500, "status": "paused",
                    }
                    if adef["reports_to"] and adef["reports_to"] in created_by_role:
                        agent_json["reportsTo"] = created_by_role[adef["reports_to"]]
                    r = httpx.post(f"{PAPERCLIP_URL}/companies/{company_id}/agents", json=agent_json, timeout=10)
                    if r.status_code in (200, 201):
                        aid = r.json()["id"]
                        new_agent_ids[adef["hermes_id"]] = aid
                        created_by_role[adef["role"]] = aid
                except Exception:
                    pass
            # Update config with new IDs
            if new_agent_ids:
                config[company_key]["agents"] = new_agent_ids
                CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
            agents = _paperclip_get(f"/companies/{company_id}/agents") or []

        # Find agent IDs by role for assignment
        role_to_agent = {}
        for a in agents:
            role_to_agent[a.get("role", "")] = a["id"]
        # Create fresh Round 1 tasks
        for task in round1_tasks.get(company_key, []):
            assignee = role_to_agent.get(task["role"], "")
            try:
                httpx.post(f"{PAPERCLIP_URL}/companies/{company_id}/issues", json={
                    "title": task["title"],
                    "description": task["description"],
                    "status": "todo",
                    "priority": "high",
                    "assigneeAgentId": assignee or None,
                }, timeout=5.0)
            except Exception:
                pass

    # 4. Archive ALL companies except current ones, then activate current
    blitz_id = config.get("blitz", {}).get("company_id")
    craft_id = config.get("craft", {}).get("company_id")
    current_ids = {blitz_id, craft_id}

    companies = _paperclip_get("/companies") or []
    for c in companies:
        if c["id"] not in current_ids:
            # Terminate agents (permanent) then archive company
            for a in (_paperclip_get(f"/companies/{c['id']}/agents") or []):
                if a.get("status") not in ("terminated",):
                    _paperclip_patch(f"/agents/{a['id']}", {"status": "terminated"})
            if c.get("status") != "archived":
                _paperclip_patch(f"/companies/{c['id']}", {"status": "archived"})

    if blitz_id:
        _paperclip_patch(f"/companies/{blitz_id}", {"status": "active"})
    if craft_id:
        _paperclip_patch(f"/companies/{craft_id}", {"status": "active"})

    timing = _get_timing()

    # 5. Wipe evidence.db
    conn = sqlite3.connect(str(db_path))
    for table in ("skill_snapshots", "memory_snapshots", "heartbeat_metrics",
                  "skill_usage_events", "learning_milestones"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    # 6. Wipe agent HERMES_HOME state (ALL skills + memory) and optimize per role
    hermes_base = Path.home() / ".hermes" / "gladiator"
    # Role-based toolset optimization: non-code agents get fewer tools → fewer
    # tool definitions in system prompt → saves ~2000 input tokens per API call
    # ALL agents need terminal for curl (Paperclip API: checkout/complete tasks).
    # Non-code agents skip 'file' toolset (read_file, write_file, patch, search).
    ROLE_TOOLSETS = {
        "ceo": "terminal,web,skills,memory",
        "cmo": "terminal,web,skills,memory",
        "general": "terminal,web,skills,memory",          # Content, Docs
        "cto": "terminal,file,web,skills,memory",
        "engineer": "terminal,file,web,skills,memory",
        "qa": "terminal,file,web,skills,memory",
    }
    ROLE_ITERATIONS = {
        "ceo": 15, "cmo": 15, "general": 15,     # simple tasks
        "cto": 30, "engineer": 30, "qa": 30,      # code tasks
    }
    ROLE_TIMEOUT = {
        "ceo": 300, "cmo": 300, "general": 300,
        "cto": 600, "engineer": 600, "qa": 600,
    }

    for company_key in ("blitz", "craft"):
        company_id = config.get(company_key, {}).get("company_id")
        agents_list = _paperclip_get(f"/companies/{company_id}/agents") or [] if company_id else []
        for agent_key in config.get(company_key, {}).get("agents", {}).keys():
            agent_home = hermes_base / agent_key
            # Clear memory
            for mem_name in ("MEMORY.md", "USER.md"):
                mem_file = agent_home / "memories" / mem_name
                if mem_file.exists():
                    mem_file.write_text("")
            # Delete ALL skills (including bundled) — bundled skills waste ~800
            # tokens of system prompt on irrelevant skill index entries per API call.
            # Agents only need custom skills they create during competition.
            skills_dir = agent_home / "skills"
            if skills_dir.exists():
                for skill_dir in skills_dir.iterdir():
                    if skill_dir.is_dir():
                        shutil.rmtree(str(skill_dir), ignore_errors=True)
                # Clear bundled manifest so Hermes doesn't try to reload them
                manifest = skills_dir / ".bundled_manifest"
                if manifest.exists():
                    manifest.write_text("")
            # Clear sessions
            sessions_dir = agent_home / "sessions"
            if sessions_dir.exists():
                for sf in sessions_dir.glob("session_*.json"):
                    sf.unlink()
            # Clear error logs
            err_log = agent_home / "logs" / "errors.log"
            if err_log.exists():
                err_log.write_text("")

        # PATCH each agent's adapterConfig with role-optimized toolsets and limits
        for a in agents_list:
            role = a.get("role", "engineer")
            toolsets = ROLE_TOOLSETS.get(role, "terminal,file,web,skills,memory")
            max_iter = ROLE_ITERATIONS.get(role, 30)
            timeout = ROLE_TIMEOUT.get(role, 600)
            existing_config = a.get("adapterConfig", {})
            _paperclip_patch(f"/agents/{a['id']}", {
                "adapterConfig": {
                    **existing_config,
                    "enabledToolsets": toolsets.split(","),
                    "maxIterations": max_iter,
                    "timeoutSec": timeout,
                },
            })

    # 7. Restart watcher so it picks up fresh state (reset in-memory counters)
    import subprocess as _sp
    _sp.run(["pkill", "-f", "watcher.py"], capture_output=True)
    time.sleep(0.5)
    watcher_poll = str(timing.get("watcher_poll_seconds", 5))
    _sp.Popen(
        [str(Path(__file__).parent.parent / "base-product" / ".venv" / "bin" / "python"),
         "-u", str(Path(__file__).parent.parent / "traces" / "watcher.py"), watcher_poll],
        cwd=str(Path(__file__).parent.parent),
        stdout=open("/tmp/watcher.log", "w"), stderr=_sp.STDOUT,
    )

    # 8. Reset git repos so agent commits start fresh
    base_product = Path(__file__).parent.parent / "base-product"
    for repo_dir in ("company_a/repo", "company_b/repo"):
        repo_path = Path(__file__).parent.parent / repo_dir
        if not repo_path.exists():
            continue
        try:
            git_dir = repo_path / ".git"
            if git_dir.exists():
                # Has git history — try reset to base commit, fallback to re-init
                result = _sp.run(["git", "rev-parse", "--verify", "89d0c27"],
                                 cwd=str(repo_path), capture_output=True, timeout=5)
                if result.returncode == 0:
                    _sp.run(["git", "reset", "--hard", "89d0c27"], cwd=str(repo_path),
                            capture_output=True, timeout=5)
                    _sp.run(["git", "clean", "-fd"], cwd=str(repo_path),
                            capture_output=True, timeout=5)
                    continue
            # No git or no base commit — wipe agent artifacts, init fresh
            # Remove agent-created files but keep base product source
            for item in repo_path.iterdir():
                if item.name in ("src", "setup.py", "README.md"):
                    continue
                if item.name == ".git":
                    shutil.rmtree(str(item), ignore_errors=True)
                    continue
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    shutil.rmtree(str(item), ignore_errors=True)
                else:
                    item.unlink()
            # Copy base product source to reset code to canonical state
            for src_file in (base_product / "src" / "llm_judge").glob("*.py"):
                dest = repo_path / "src" / "llm_judge" / src_file.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_file), str(dest))
            # Init git so agents can commit
            _sp.run(["git", "init"], cwd=str(repo_path), capture_output=True, timeout=5)
            _sp.run(["git", "add", "."], cwd=str(repo_path), capture_output=True, timeout=5)
            _sp.run(["git", "commit", "-m", "Initial llm-judge v0.1.0"],
                     cwd=str(repo_path), capture_output=True, timeout=5)
        except Exception:
            pass

    # 9. Set start_time — competition officially begins now
    start_time = time.time()
    competition_active = True

    # 10. Wake up ALL agents after delay — but only if competition is still active
    wakeup_start_time = start_time  # capture current start_time to detect if reset was called again

    async def delayed_wakeup():
        await asyncio.sleep(5)
        # Guard: only proceed if this is still the same competition
        if start_time != wakeup_start_time or not competition_active:
            return
        for ck in ("blitz", "craft"):
            for ak, aid in config.get(ck, {}).get("agents", {}).items():
                try:
                    httpx.patch(f"{PAPERCLIP_URL}/agents/{aid}",
                                json={"status": "idle"}, timeout=5.0)
                    httpx.post(f"{PAPERCLIP_URL}/agents/{aid}/wakeup",
                               json={"source": "on_demand", "reason": "Competition started"},
                               timeout=5.0)
                except Exception:
                    pass
    asyncio.create_task(delayed_wakeup())

    return {"status": "reset", "timing": timing,
            "message": "Full reset complete. Watcher restarted. Agents wake up in 5 seconds."}


@app.get("/api/config")
async def get_config():
    """Return current timing configuration."""
    return _get_timing()


# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)
