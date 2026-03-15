"""Post-competition merger: combine Blitz + Craft into Gladiator United."""
import json
import shutil
import re
from pathlib import Path
from datetime import datetime

import httpx

PAPERCLIP_URL = "http://localhost:3100/api"
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "gladiator_config.json"
HERMES_GLADIATOR_HOME = Path.home() / ".hermes" / "gladiator"


def api(method: str, path: str, data: dict | None = None):
    resp = httpx.request(method, f"{PAPERCLIP_URL}{path}", json=data, timeout=10.0)
    if resp.status_code >= 400:
        print(f"  WARN: {method} {path} → {resp.status_code}")
        return None
    return resp.json()


def merge_skills() -> dict:
    """Merge skill libraries from both companies. Keep highest version on conflict."""
    merged: dict[str, dict] = {}  # skill_name → {version, content, source_agent, path}

    for agent_dir in sorted(HERMES_GLADIATOR_HOME.iterdir()):
        if not agent_dir.is_dir():
            continue
        skills_dir = agent_dir / "skills"
        if not skills_dir.exists():
            continue

        for skill_md in skills_dir.rglob("SKILL.md"):
            skill_name = skill_md.parent.name
            content = skill_md.read_text(errors="replace")

            # Extract version
            version = "0.0.0"
            match = re.search(r"^version:\s*(.+)$", content, re.MULTILINE)
            if match:
                version = match.group(1).strip().strip("'\"")

            existing = merged.get(skill_name)
            if existing is None or _version_gt(version, existing["version"]):
                merged[skill_name] = {
                    "version": version,
                    "content": content,
                    "source_agent": agent_dir.name,
                    "source_path": str(skill_md),
                }

    return merged


def _version_gt(a: str, b: str) -> bool:
    """Compare semver-ish strings."""
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v)]
    return parts(a) > parts(b)


def create_united_company(config: dict, merged_skills: dict) -> str:
    """Create Gladiator United in Paperclip."""
    # Get stats from both companies
    blitz = api("GET", f"/companies/{config['blitz']['company_id']}") or {}
    craft = api("GET", f"/companies/{config['craft']['company_id']}") or {}

    blitz_agents = api("GET", f"/companies/{config['blitz']['company_id']}/agents") or []
    craft_agents = api("GET", f"/companies/{config['craft']['company_id']}/agents") or []

    # Create united company
    united = api("POST", "/companies", {
        "name": "Gladiator United",
        "mission": (
            "The two rival companies have merged. Blitz's growth tactics "
            "combined with Craft's engineering excellence. New goal: 1000 stars. "
            f"Merged skill library: {len(merged_skills)} unique skills from {len(blitz_agents) + len(craft_agents)} agents."
        ),
    })

    if not united:
        print("ERROR: Failed to create united company")
        return ""

    united_id = united["id"]
    api("PATCH", f"/companies/{united_id}", {
        "budgetMonthlyCents": 4000,  # $40 combined
    })

    print(f"  Created 'Gladiator United': {united_id}")

    # Recreate all agents under united company
    ceo = None
    for agent in blitz_agents + craft_agents:
        origin = "blitz" if agent["companyId"] == config["blitz"]["company_id"] else "craft"
        new_name = f"{agent['name']} (ex-{origin.title()})"

        new_agent = api("POST", f"/companies/{united_id}/agents", {
            "name": new_name,
            "role": agent["role"],
            "adapterType": agent["adapterType"],
            "adapterConfig": agent.get("adapterConfig", {}),
            "jobDescription": f"Former {origin.title()} agent, now part of Gladiator United.",
            "heartbeatIntervalSeconds": config.get("timing", {}).get("heartbeat_engineer_seconds", 1800),
            "budgetMonthlyCents": agent.get("budgetMonthlyCents", 200),
        })

        if new_agent and agent["role"] == "ceo" and ceo is None:
            ceo = new_agent
            print(f"  CEO: {new_name}")
        elif new_agent:
            # Set reporting to CEO
            if ceo:
                api("PATCH", f"/agents/{new_agent['id']}", {"reportsTo": ceo["id"]})
            print(f"  Agent: {new_name}")

    return united_id


def write_merged_skills(merged_skills: dict):
    """Write merged skill library to a united HERMES_HOME."""
    united_home = HERMES_GLADIATOR_HOME / "united"
    skills_dir = united_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (united_home / "memories").mkdir(exist_ok=True)

    for skill_name, skill_data in merged_skills.items():
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_data["content"])

    print(f"  Merged skills written to {skills_dir}")
    return united_home


def generate_merge_report(config: dict, merged_skills: dict, united_id: str) -> dict:
    """Generate a merge report for the dashboard."""
    report = {
        "merged_at": datetime.utcnow().isoformat(),
        "united_company_id": united_id,
        "skills_merged": len(merged_skills),
        "skills_detail": {
            name: {
                "version": data["version"],
                "source_agent": data["source_agent"],
            }
            for name, data in merged_skills.items()
        },
        "blitz_company_id": config["blitz"]["company_id"],
        "craft_company_id": config["craft"]["company_id"],
    }

    report_path = PROJECT_ROOT / "merge_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  Merge report: {report_path}")
    return report


def main():
    print("=" * 50)
    print("GLADIATOR MERGE — Two Companies Become One")
    print("=" * 50)

    if not CONFIG_PATH.exists():
        print("ERROR: gladiator_config.json not found. Run setup_companies.py first.")
        return

    config = json.loads(CONFIG_PATH.read_text())

    # Step 1: Merge skill libraries
    print("\n1. Merging skill libraries...")
    merged_skills = merge_skills()
    print(f"   Found {len(merged_skills)} unique skills")
    for name, data in sorted(merged_skills.items()):
        print(f"   - {name} v{data['version']} (from {data['source_agent']})")

    # Step 2: Write merged skills
    print("\n2. Writing merged skill library...")
    united_home = write_merged_skills(merged_skills)

    # Step 3: Create united company in Paperclip
    print("\n3. Creating Gladiator United in Paperclip...")
    united_id = create_united_company(config, merged_skills)

    # Step 4: Archive original companies
    print("\n4. Archiving original companies...")
    api("POST", f"/companies/{config['blitz']['company_id']}/archive")
    print("   Blitz → archived")
    api("POST", f"/companies/{config['craft']['company_id']}/archive")
    print("   Craft → archived")

    # Step 5: Generate report
    print("\n5. Generating merge report...")
    report = generate_merge_report(config, merged_skills, united_id)

    # Update config
    config["united"] = {
        "company_id": united_id,
        "hermes_home": str(united_home),
        "merged_at": report["merged_at"],
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))

    print("\n" + "=" * 50)
    print("MERGE COMPLETE")
    print("=" * 50)
    print(f"  Skills merged: {len(merged_skills)}")
    print(f"  United company: {united_id}")
    print(f"  Paperclip UI: http://localhost:3100")


if __name__ == "__main__":
    main()
