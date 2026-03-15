"""Heartbeat watcher: polls Paperclip for completed runs and captures evidence."""
import json
import time
import sys
from pathlib import Path
from datetime import datetime

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from traces.db import init_db, get_db, DB_PATH
from traces.collector import register_agent, collect_all

PAPERCLIP_URL = "http://localhost:3100/api"
CONFIG_PATH = Path(__file__).parent.parent / "gladiator_config.json"
HERMES_GLADIATOR_HOME = Path.home() / ".hermes" / "gladiator"

# Track which runs we've already processed
processed_runs: set[str] = set()
# Track last known heartbeat timestamps per agent
last_heartbeat: dict[str, str] = {}


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def api_get(path: str):
    try:
        resp = httpx.get(f"{PAPERCLIP_URL}{path}", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"  API error: {e}")
    return None


def register_all_agents(config: dict):
    """Register all agents with the evidence collector."""
    for company_key in ("blitz", "craft"):
        agents = config.get(company_key, {}).get("agents", {})
        for agent_key, agent_id in agents.items():
            hermes_home = HERMES_GLADIATOR_HOME / agent_key
            register_agent(agent_key, company_key, hermes_home)
            print(f"  Registered: {agent_key} ({company_key})")


def estimate_tokens_from_session(agent_key: str) -> dict:
    """Estimate token usage from Hermes session log files.

    Hermes doesn't report tokens back to Paperclip, so we estimate from
    session file content. ~4 chars per token is a reasonable approximation.
    """
    sessions_dir = HERMES_GLADIATOR_HOME / agent_key / "sessions"
    if not sessions_dir.exists():
        return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "session_id": None}

    session_files = sorted(sessions_dir.glob("session_*.json"), reverse=True)
    if not session_files:
        return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "session_id": None}

    latest = session_files[0]
    try:
        data = json.loads(latest.read_text())
        messages = data.get("messages", [])
        model = data.get("model", "claude-haiku-4-5-20251001")
        session_id = data.get("session_id")

        input_chars = 0
        output_chars = 0
        # Count system prompt
        sys_prompt = data.get("system_prompt", "")
        if sys_prompt:
            input_chars += len(sys_prompt)

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            chars = len(content) if isinstance(content, str) else 0

            if msg.get("role") in ("user", "system"):
                input_chars += chars
            elif msg.get("role") == "assistant":
                output_chars += chars
            elif msg.get("role") == "tool":
                input_chars += chars

        # ~4 chars per token
        tokens_in = max(input_chars // 4, 1)
        tokens_out = max(output_chars // 4, 1)

        # Cost calculation (per million tokens)
        if "sonnet" in model:
            cost_usd = (tokens_in * 3.0 / 1_000_000) + (tokens_out * 15.0 / 1_000_000)
        elif "haiku" in model:
            cost_usd = (tokens_in * 0.80 / 1_000_000) + (tokens_out * 4.0 / 1_000_000)
        else:
            cost_usd = (tokens_in * 3.0 / 1_000_000) + (tokens_out * 15.0 / 1_000_000)

        return {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd, 6),
            "session_id": session_id,
        }
    except Exception as e:
        print(f"    Error parsing session for {agent_key}: {e}")
        return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "session_id": None}


def check_for_completed_heartbeats(config: dict):
    """Poll Paperclip for agents that have completed heartbeats since last check."""
    new_completions = []

    for company_key in ("blitz", "craft"):
        company_id = config[company_key]["company_id"]
        agents = api_get(f"/companies/{company_id}/agents")
        if not agents:
            continue

        for agent in agents:
            agent_id = agent["id"]
            hb_at = agent.get("lastHeartbeatAt")

            if not hb_at:
                continue

            # Find the agent_key (hermes_id) for this agent
            agent_key = None
            for key, aid in config[company_key]["agents"].items():
                if aid == agent_id:
                    agent_key = key
                    break

            if not agent_key:
                continue

            # Check if this is a new heartbeat
            prev_hb = last_heartbeat.get(agent_key)
            if prev_hb == hb_at:
                continue

            last_heartbeat[agent_key] = hb_at

            # Get runtime state for metrics
            runtime = api_get(f"/agents/{agent_id}/runtime-state")
            if not runtime:
                continue

            run_status = runtime.get("lastRunStatus", "unknown")
            if run_status != "succeeded":
                print(f"  [{agent_key}] Run status: {run_status} (skipping)")
                continue

            # Paperclip doesn't have token data (Hermes doesn't report back).
            # Estimate from Hermes session logs instead.
            token_data = estimate_tokens_from_session(agent_key)

            session_id = token_data.get("session_id")
            if not session_id:
                session_id = runtime.get("sessionId")
                session_params = runtime.get("sessionParamsJson")
                if session_params and isinstance(session_params, dict):
                    session_id = session_params.get("sessionId", session_id)

            new_completions.append({
                "agent_key": agent_key,
                "company": company_key,
                "session_id": session_id,
                "tokens_in": token_data["tokens_in"],
                "tokens_out": token_data["tokens_out"],
                "cost_cents": int(round(token_data["cost_usd"] * 100)),
                "heartbeat_at": hb_at,
            })

    return new_completions


def process_completions(completions: list[dict]):
    """Run evidence collection for each completed heartbeat."""
    for comp in completions:
        agent_key = comp["agent_key"]
        print(f"  Collecting evidence for {agent_key}...")

        # Read agent's output from the most recent session file
        hermes_home = HERMES_GLADIATOR_HOME / agent_key
        output_text = ""
        sessions_dir = hermes_home / "sessions"
        if sessions_dir.exists():
            session_files = sorted(sessions_dir.glob("session_*.json"), reverse=True)
            if session_files:
                try:
                    session_data = json.loads(session_files[0].read_text())
                    messages = session_data.get("messages", [])
                    for msg in messages:
                        if isinstance(msg, dict) and msg.get("role") == "assistant":
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                content = " ".join(
                                    c.get("text", "") for c in content if isinstance(c, dict)
                                )
                            if isinstance(content, str):
                                output_text += content + "\n"
                except Exception:
                    pass

        # Keep only latest 2 sessions to prevent stale resume corruption
        if sessions_dir.exists():
            session_files = sorted(sessions_dir.glob("session_*.json"), reverse=True)
            for old_session in session_files[2:]:
                try:
                    old_session.unlink()
                except Exception:
                    pass

        collect_all(
            agent_id=agent_key,
            output_text=output_text,
            session_id=comp.get("session_id"),
            tokens_in=comp.get("tokens_in", 0),
            tokens_out=comp.get("tokens_out", 0),
            cost_usd=comp.get("cost_cents", 0) / 100.0,
            duration_ms=0,  # Not available from runtime state
            task_summary=f"Heartbeat at {comp['heartbeat_at']}",
        )
        print(f"    Skills, memory, metrics captured for {agent_key}")


def run_once(config: dict):
    """Single poll cycle."""
    completions = check_for_completed_heartbeats(config)
    if completions:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {len(completions)} new heartbeat(s) detected")
        process_completions(completions)
    return len(completions)


def main():
    print("=" * 50)
    print("GLADIATOR Evidence Watcher")
    print("=" * 50)

    init_db()
    config = load_config()

    print("\nRegistering agents...")
    register_all_agents(config)

    # Do an initial snapshot of all agents' current state
    print("\nTaking initial snapshots...")
    from traces.collector import snapshot_skills, snapshot_memory
    for company_key in ("blitz", "craft"):
        for agent_key in config[company_key]["agents"]:
            snapshot_skills(agent_key)
            snapshot_memory(agent_key)
    print("  Initial snapshots done")

    poll_interval = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    print(f"\nPolling every {poll_interval}s for completed heartbeats...")
    print("Press Ctrl+C to stop\n")

    try:
        while True:
            run_once(config)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nWatcher stopped.")

    # Final report
    from traces.analyzer import generate_learning_report
    report = generate_learning_report()
    print(f"\nFinal stats:")
    for company in ("blitz", "craft"):
        s = report["summary"].get(company, {})
        print(f"  {company}: {s.get('total_skills',0)} skills, "
              f"{s.get('total_heartbeats',0)} heartbeats, "
              f"{s.get('total_milestones',0)} milestones")


if __name__ == "__main__":
    main()
