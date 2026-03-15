"""Configure Blitz and Craft companies in Paperclip via API."""
import json
import sys
import httpx
from pathlib import Path

PAPERCLIP_URL = "http://localhost:3100/api"
PROJECT_ROOT = Path(__file__).parent.parent
HERMES_GLADIATOR_HOME = Path.home() / ".hermes" / "gladiator"


def api(method: str, path: str, data: dict | None = None) -> dict:
    """Make a Paperclip API call."""
    url = f"{PAPERCLIP_URL}{path}"
    resp = httpx.request(method, url, json=data, timeout=10.0)
    if resp.status_code >= 400:
        print(f"  ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)
    return resp.json()


def read_soul(company_dir: str, agent_dir: str) -> str:
    """Read SOUL.md for an agent."""
    path = PROJECT_ROOT / company_dir / "agents" / agent_dir / "SOUL.md"
    return path.read_text()


def setup_hermes_homes(agents: list[dict]):
    """Create isolated HERMES_HOME directories for each agent."""
    for agent in agents:
        home = HERMES_GLADIATOR_HOME / agent["hermes_id"]
        (home / "memories").mkdir(parents=True, exist_ok=True)
        (home / "skills").mkdir(parents=True, exist_ok=True)

        # Copy global hermes config
        global_env = Path.home() / ".hermes" / ".env"
        local_env = home / ".env"
        if global_env.exists() and not local_env.exists():
            local_env.write_text(global_env.read_text())

        global_config = Path.home() / ".hermes" / "config.yaml"
        local_config = home / "config.yaml"
        if global_config.exists() and not local_config.exists():
            local_config.write_text(global_config.read_text())

        # Write SOUL.md as the agent's personality
        soul_path = home / "SOUL.md"
        soul_path.write_text(agent["soul_content"])

        print(f"  HERMES_HOME: {home}")


def create_company(name: str, mission: str, budget_cents: int) -> str:
    """Create a company and return its ID."""
    company = api("POST", "/companies", {
        "name": name,
        "mission": mission,
    })
    company_id = company["id"]

    # Set budget
    api("PATCH", f"/companies/{company_id}", {
        "budgetMonthlyCents": budget_cents,
    })

    print(f"  Company '{name}' created: {company_id}")
    return company_id


def create_agent(
    company_id: str,
    name: str,
    role: str,
    hermes_id: str,
    model: str,
    heartbeat_seconds: int,
    budget_cents: int,
    reports_to: str | None,
    soul_content: str,
    toolsets: list[str] | None = None,
) -> dict:
    """Create an agent in Paperclip."""
    agent_data = {
        "name": name,
        "role": role,
        "adapterType": "hermes_local",
        "adapterConfig": {
            "model": model,
            "provider": "anthropic",
            "maxIterations": 30,
            "timeoutSec": 600,
            "persistSession": True,
            "enabledToolsets": toolsets or ["terminal", "file", "web", "skills"],
            "env": {
                "HERMES_HOME": str(HERMES_GLADIATOR_HOME / hermes_id),
            },
        },
        "jobDescription": soul_content[:500],
        "heartbeatIntervalSeconds": heartbeat_seconds,
        "budgetMonthlyCents": budget_cents,
    }

    if reports_to:
        agent_data["reportsTo"] = reports_to

    agent = api("POST", f"/companies/{company_id}/agents", agent_data)
    print(f"  Agent '{name}' ({role}): {agent['id']}")
    return agent


def main():
    print("Checking Paperclip health...")
    health = api("GET", "/health")
    if health.get("status") != "ok":
        print("Paperclip is not healthy!")
        sys.exit(1)
    print("  OK\n")

    # Check for existing companies
    existing = api("GET", "/companies")
    active = [c for c in existing if c["status"] == "active"]
    if active:
        print(f"Found {len(active)} existing active companies:")
        for c in active:
            print(f"  - {c['name']} ({c['id']})")
        print("Archive them first if you want to start fresh.")
        print("Continuing will create NEW companies.\n")

    # =============================================
    # Company A — Blitz (Growth Hacking)
    # =============================================
    print("=" * 50)
    print("Setting up Company A: BLITZ (Growth Hacking)")
    print("=" * 50)

    blitz_id = create_company(
        "Blitz",
        "Reach maximum GitHub stars in 72 hours through aggressive growth tactics. "
        "Distribution beats product. Ship fast, market hard, measure everything.",
        2000,  # $20 budget in cents
    )

    blitz_agents = [
        {
            "name": "Blitz CEO",
            "role": "ceo",
            "hermes_id": "blitz-ceo",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,  # 30 min
            "budget_cents": 500,
            "reports_to": None,
            "soul_dir": "ceo",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
        {
            "name": "Blitz CMO",
            "role": "cmo",
            "hermes_id": "blitz-cmo",
            "model": "claude-haiku-4-5-20251001",
            "heartbeat_seconds": 3600,  # 60 min
            "budget_cents": 300,
            "reports_to": None,  # Will be set after CEO is created
            "soul_dir": "cmo",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
        {
            "name": "Blitz Content",
            "role": "general",
            "hermes_id": "blitz-content",
            "model": "claude-haiku-4-5-20251001",
            "heartbeat_seconds": 3600,
            "budget_cents": 200,
            "reports_to": None,  # CMO
            "soul_dir": "content",
            "toolsets": ["file", "web", "skills"],
        },
        {
            "name": "Blitz Engineer",
            "role": "engineer",
            "hermes_id": "blitz-engineer",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,
            "budget_cents": 500,
            "reports_to": None,  # CEO
            "soul_dir": "engineer",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
    ]

    # Read SOUL.md content
    for a in blitz_agents:
        a["soul_content"] = read_soul("company_a", a["soul_dir"])

    # Setup HERMES_HOME dirs
    print("\nSetting up HERMES_HOME directories...")
    setup_hermes_homes(blitz_agents)

    # Create agents (need to chain reports_to)
    print("\nCreating agents...")
    created_blitz = {}
    for a in blitz_agents:
        reports_to = None
        if a["hermes_id"] == "blitz-cmo":
            reports_to = created_blitz.get("blitz-ceo", {}).get("id")
        elif a["hermes_id"] == "blitz-content":
            reports_to = created_blitz.get("blitz-cmo", {}).get("id")
        elif a["hermes_id"] == "blitz-engineer":
            reports_to = created_blitz.get("blitz-ceo", {}).get("id")

        agent = create_agent(
            company_id=blitz_id,
            name=a["name"],
            role=a["role"],
            hermes_id=a["hermes_id"],
            model=a["model"],
            heartbeat_seconds=a["heartbeat_seconds"],
            budget_cents=a["budget_cents"],
            reports_to=reports_to,
            soul_content=a["soul_content"],
            toolsets=a.get("toolsets"),
        )
        created_blitz[a["hermes_id"]] = agent

    # =============================================
    # Company B — Craft (Technical Excellence)
    # =============================================
    print("\n" + "=" * 50)
    print("Setting up Company B: CRAFT (Technical Excellence)")
    print("=" * 50)

    craft_id = create_company(
        "Craft",
        "Reach maximum GitHub stars in 72 hours through genuine technical value. "
        "Product beats distribution. Make something so good people share it naturally.",
        2000,
    )

    craft_agents = [
        {
            "name": "Craft CEO",
            "role": "ceo",
            "hermes_id": "craft-ceo",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,
            "budget_cents": 500,
            "reports_to": None,
            "soul_dir": "ceo",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
        {
            "name": "Craft CTO",
            "role": "cto",
            "hermes_id": "craft-cto",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,
            "budget_cents": 500,
            "reports_to": None,
            "soul_dir": "cto",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
        {
            "name": "Craft Engineer 1",
            "role": "engineer",
            "hermes_id": "craft-eng1",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,
            "budget_cents": 500,
            "reports_to": None,
            "soul_dir": "engineer_1",
            "toolsets": ["terminal", "file", "web", "skills"],
        },
        {
            "name": "Craft Engineer 2",
            "role": "qa",
            "hermes_id": "craft-eng2",
            "model": "claude-sonnet-4-20250514",
            "heartbeat_seconds": 1800,
            "budget_cents": 300,
            "reports_to": None,
            "soul_dir": "engineer_2",
            "toolsets": ["terminal", "file", "skills"],
        },
        {
            "name": "Craft Docs",
            "role": "general",
            "hermes_id": "craft-docs",
            "model": "claude-haiku-4-5-20251001",
            "heartbeat_seconds": 3600,
            "budget_cents": 200,
            "reports_to": None,
            "soul_dir": "docs",
            "toolsets": ["file", "web", "skills"],
        },
    ]

    for a in craft_agents:
        a["soul_content"] = read_soul("company_b", a["soul_dir"])

    print("\nSetting up HERMES_HOME directories...")
    setup_hermes_homes(craft_agents)

    print("\nCreating agents...")
    created_craft = {}
    for a in craft_agents:
        reports_to = None
        if a["hermes_id"] == "craft-cto":
            reports_to = created_craft.get("craft-ceo", {}).get("id")
        elif a["hermes_id"] in ("craft-eng1", "craft-eng2", "craft-docs"):
            reports_to = created_craft.get("craft-cto", {}).get("id")

        agent = create_agent(
            company_id=craft_id,
            name=a["name"],
            role=a["role"],
            hermes_id=a["hermes_id"],
            model=a["model"],
            heartbeat_seconds=a["heartbeat_seconds"],
            budget_cents=a["budget_cents"],
            reports_to=reports_to,
            soul_content=a["soul_content"],
            toolsets=a.get("toolsets"),
        )
        created_craft[a["hermes_id"]] = agent

    # =============================================
    # Save company IDs for other scripts
    # =============================================
    config = {
        "blitz": {
            "company_id": blitz_id,
            "agents": {k: v["id"] for k, v in created_blitz.items()},
        },
        "craft": {
            "company_id": craft_id,
            "agents": {k: v["id"] for k, v in created_craft.items()},
        },
    }
    config_path = PROJECT_ROOT / "gladiator_config.json"
    config_path.write_text(json.dumps(config, indent=2))
    print(f"\nConfig saved to {config_path}")

    print("\n" + "=" * 50)
    print("SETUP COMPLETE")
    print("=" * 50)
    print(f"Blitz: {len(created_blitz)} agents")
    print(f"Craft: {len(created_craft)} agents")
    print(f"\nPaperclip UI: http://localhost:3100")
    print(f"Config: {config_path}")


if __name__ == "__main__":
    main()
