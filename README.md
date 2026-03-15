# GLADIATOR

**Two zero-human AI companies. Same product. Different strategies. One winner.**

Gladiator pits two autonomous AI companies (**Blitz**, growth-first, vs **Craft**, quality-first) against each other to maximize GitHub stars on identical starter repos. Powered by [Paperclip](https://github.com/paperclipai/paperclip) orchestration and [Hermes Agent](https://github.com/NousResearch/hermes-agent) workers. A live dashboard visualizes the battle in real-time.

Built for the [Nous Research Hermes Agent Hackathon](https://nous.hermes.dev/) (March 2026).

---

## What You'll See

1. **Landing page**: explains the product (llm-judge), the rules and the two rival companies
2. **Live dashboard**: 9-section narrative: scoreboard, task boards, code comparison, Gantt chart, audit trail, learning evidence, merge controls
3. **Competition**: 9 agents complete 10 tasks in ~5-6 minutes, creating skills, growing memory and writing code
4. **Winner announcement**: auto-detects completion, declares winner by projected GitHub stars
5. **Merge**: companies unite, skills transfer across teams, proving Hermes learning is real

## Architecture

```
┌─────────────┐     heartbeats      ┌──────────────────┐
│  Paperclip  │ ──────────────────► │   Hermes Agent   │
│  :3100      │ ◄────────────────── │   (9 instances)  │
│  (orchestr) │   task completion   │   Claude Sonnet  │
└──────┬──────┘                     └────────┬─────────┘
       │                                     │
       │ company/agent/issue APIs            │ skills, memory, sessions
       │                                     │ in ~/.hermes/gladiator/
┌──────┴──────┐     evidence.db     ┌────────┴─────────┐
│  Dashboard  │ ◄────────────────── │    Watcher        │
│  :4000      │                     │  (polls every 5s) │
│  (FastAPI)  │                     └──────────────────┘
└─────────────┘
```

| Component | Tech | Port |
|-----------|------|------|
| **Paperclip** | Node.js + PostgreSQL 16 | 3100 |
| **Hermes Agent** | Python CLI + Anthropic API | - |
| **Dashboard** | FastAPI + SSE + vanilla JS | 4000 |
| **Evidence DB** | SQLite (WAL mode) | - |
| **Watcher** | Python daemon | - |

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Python** | 3.11+ | For Hermes Agent + Gladiator dashboard |
| **Node.js** | 20+ | For Paperclip |
| **pnpm** | 9+ | Paperclip uses pnpm workspaces |
| **PostgreSQL** | 16+ | Paperclip's data store |
| **Anthropic API key** | - | Claude Sonnet/Haiku access |
| **~$5-6 USD** | - | Per 10-minute competition run (9 Sonnet agents) |

---

## Setup Guide

### Step 1: Install Hermes Agent

```bash
# Official installer
curl -fsSL https://hermes.nousresearch.com/install.sh | bash

# Verify
hermes --version
# Expected: Hermes Agent v0.2.0+

# Configure Anthropic provider
cat > ~/.hermes/.env << 'EOF'
ANTHROPIC_API_KEY=your-key-here
EOF

# Test
hermes chat -q "Say hello in 5 words" -Q --provider anthropic -m claude-haiku-4-5-20251001
```

### Step 2: Install & Configure Paperclip

```bash
# Clone Paperclip
cd ~
git clone https://github.com/paperclipai/paperclip.git
cd paperclip

# Install dependencies (includes hermes-paperclip-adapter@0.1.1)
pnpm install
```

**PostgreSQL setup:**

```bash
# Create database and user
sudo -u postgres psql -c "CREATE USER paperclip WITH PASSWORD 'paperclip';"
sudo -u postgres psql -c "CREATE DATABASE paperclip OWNER paperclip;"

# On WSL2, if PostgreSQL isn't running:
sudo service postgresql start
```

**Apply the Hermes adapter** (if not already in your Paperclip version):

The `hermes-paperclip-adapter@0.1.1` npm package provides the integration. Paperclip needs three things:

1. Add `"hermes_local"` to `AGENT_ADAPTER_TYPES` in `packages/shared/src/constants.ts`
2. Add `"hermes-paperclip-adapter": "0.1.1"` to `server/package.json` dependencies
3. Import and register the adapter in `server/src/adapters/registry.ts`:

```typescript
// Add imports
import {
  execute as hermesExecute,
  testEnvironment as hermesTestEnvironment,
  sessionCodec as hermesSessionCodec,
} from "hermes-paperclip-adapter/server";
import {
  agentConfigurationDoc as hermesAgentConfigurationDoc,
  models as hermesModels,
} from "hermes-paperclip-adapter";

// Add adapter definition
const hermesLocalAdapter: ServerAdapterModule = {
  type: "hermes_local",
  execute: hermesExecute,
  testEnvironment: hermesTestEnvironment,
  sessionCodec: hermesSessionCodec,
  models: hermesModels,
  supportsLocalAgentJwt: true,
  agentConfigurationDoc: hermesAgentConfigurationDoc,
};

// Add to adaptersByType map
```

Then run `pnpm install` again to fetch the adapter package.

**Known adapter patches** (may already be fixed in newer versions):

The adapter v0.1.1 had two bugs we patched locally:

1. **Env variable unwrapping**: Paperclip wraps env vars as `{"type":"plain","value":"..."}` objects. The adapter's `execute.js` needs to unwrap the `.value` property:
   ```javascript
   // In node_modules/hermes-paperclip-adapter/dist/server/execute.js
   // Find the env variable assignment loop and ensure it handles both formats:
   if (typeof v === "string") {
       env[k] = v;
   } else if (v && typeof v === "object" && typeof v.value === "string") {
       env[k] = v.value;
   }
   ```

2. **Missing Anthropic provider**: Add `"anthropic"` to `VALID_PROVIDERS` in `node_modules/hermes-paperclip-adapter/dist/shared/constants.js`:
   ```javascript
   export const VALID_PROVIDERS = [
       "auto", "anthropic", "openrouter", "nous", ...
   ];
   ```

### Step 3: Clone Gladiator

```bash
cd ~/python_projects  # or wherever you prefer
git clone https://github.com/runtimenoteslabs/gladiator.git
cd gladiator

# Create Python virtual environment
python3 -m venv base-product/.venv
source base-product/.venv/bin/activate
pip install fastapi uvicorn httpx rich sse-starlette

# Configure API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Step 4: Create Companies & Agents in Paperclip

Start Paperclip first:
```bash
cd ~/paperclip
DATABASE_URL="postgres://paperclip:paperclip@localhost:5432/paperclip" pnpm dev &
sleep 10
curl -s http://localhost:3100/api/health  # Should return {"status":"ok"}
```

Then run the setup script:
```bash
cd ~/python_projects/gladiator
./base-product/.venv/bin/python scripts/setup_companies.py
```

This creates:
- **Blitz Corp**: 4 agents (CEO, Engineer, CMO, Content)
- **Craft Labs**: 5 agents (CEO, CTO, Engineer 1, Engineer 2, Docs)
- Isolated `~/.hermes/gladiator/{agent-id}/` homes with SOUL.md personalities
- `gladiator_config.json` with all company/agent UUIDs

### Step 5: Start the Dashboard

```bash
cd ~/python_projects/gladiator
./base-product/.venv/bin/python -m uvicorn dashboard.server:app --host 0.0.0.0 --port 4000
```

Open http://localhost:4000/landing in your browser.

### Step 6: Launch Demo

Click **LAUNCH DEMO** on the landing page. Everything else is automated:
- Evidence watcher auto-starts
- Git repos auto-initialize
- 9 agents wake up after 5-second delay
- 10-minute timer begins
- Winner announced when all tasks complete (or timer expires)
- Click **MERGE** after winner to demonstrate cross-company skill transfer

---

## Running the Demo

### Quick Start (after initial setup)

```bash
# Terminal 1: Paperclip
cd ~/paperclip
DATABASE_URL="postgres://paperclip:paperclip@localhost:5432/paperclip" pnpm dev

# Terminal 2: Dashboard
cd ~/python_projects/gladiator
./base-product/.venv/bin/python -m uvicorn dashboard.server:app --host 0.0.0.0 --port 4000

# Browser: http://localhost:4000/landing → Click LAUNCH DEMO
```

### What Happens During a Run

| Time | What's happening |
|------|-------|
| 0:00 | Reset wipes all state, restarts watcher, inits fresh git repos |
| 0:05 | 9 agents wake up and start working on assigned tasks |
| 1:00–5:00 | Tasks complete, skills get written, memory grows, code gets committed |
| ~5:30 | All 10 tasks done, winner announced, agents paused automatically |
| +1 min | Click MERGE: companies unite, skill transfer task runs |

Each run costs roughly **$5-6 on the Anthropic API** (9 Sonnet agents doing tool-heavy work). Budget accordingly.

### Dashboard Pages

| URL | Description |
|-----|-------------|
| `/landing` | Pre-demo landing page with LAUNCH button |
| `/` | Main dashboard (9 sections + merge controls) |
| `/comparison` | Head-to-head company comparison + agent details |
| `/intel` | System checks, strategic insights, heartbeat history |

---

## Project Structure

```
gladiator/
├── base-product/           # Canonical llm-judge starter code
│   └── src/llm_judge/      # judge.py, cli.py, display.py
├── company_a/              # Blitz
│   ├── agents/             # SOUL.md personalities (source of truth)
│   │   ├── ceo/SOUL.md
│   │   ├── engineer/SOUL.md
│   │   ├── cmo/SOUL.md
│   │   └── content/SOUL.md
│   └── repo/               # Agent-modified llm-judge (gitignored, created at runtime)
├── company_b/              # Craft
│   ├── agents/             # SOUL.md personalities
│   │   ├── ceo/SOUL.md
│   │   ├── cto/SOUL.md
│   │   ├── engineer_1/SOUL.md
│   │   ├── engineer_2/SOUL.md
│   │   └── docs/SOUL.md
│   └── repo/               # Agent-modified llm-judge (gitignored, created at runtime)
├── dashboard/
│   ├── server.py           # FastAPI + SSE backend (1300+ lines)
│   └── static/             # HTML/JS/CSS frontend
├── traces/
│   ├── db.py               # SQLite schema (5 tables)
│   ├── collector.py        # Evidence collection (skills, memory, heartbeats)
│   ├── analyzer.py         # Learning report generation
│   └── watcher.py          # Paperclip heartbeat poller
├── scripts/
│   ├── setup_companies.py  # Create companies + agents in Paperclip
│   ├── merge_companies.py  # Post-competition merger
│   ├── launch.sh           # Start all services
│   └── stop.sh             # Stop all services
├── .env.example            # API key template
└── gladiator_config.example.json  # Config template
```

### Runtime State (gitignored, regenerated per run)

| File | Purpose |
|------|---------|
| `.env` | Your Anthropic API key |
| `gladiator_config.json` | Paperclip company/agent UUIDs |
| `evidence.db` | SQLite learning evidence |
| `merge_report.json` | Post-merge skill inventory |
| `~/.hermes/gladiator/` | Agent homes (skills, memory, sessions) |

---

## How It Works

### The Competition

Both companies start with identical copies of **llm-judge**, a CLI tool that compares LLM responses side-by-side. Each company's agents work autonomously to improve the product and maximize projected GitHub stars.

**Star formula:** `tasks_done × 8 + unique_skills × 5 + skill_versions × 3`

### Hermes Features Used

| Feature | How It's Used | Evidence |
|---------|---------------|----------|
| **Skills** | Agents create reusable SKILL.md files after complex tasks | `skill_snapshots` table, version diffs |
| **Memory** | Agents save strategies and learnings to MEMORY.md | `memory_snapshots` table, char growth |
| **Sessions** | Session IDs chain across heartbeats | `heartbeat_metrics.session_id` |
| **Skill Usage** | Agents reference and apply their learned skills | `skill_usage_events` table |
| **Cross-Agent Learning** | Post-merge: agents use skills from rival team | `learning_milestones` type=cross_agent |

### Paperclip Orchestration

- **Companies** define budget and organizational structure
- **Agents** are autonomous workers with roles, models and heartbeat intervals
- **Issues** are tasks assigned to agents (checkout → work → mark done)
- **Heartbeats** trigger agent execution at configured intervals
- The **hermes_local** adapter spawns `hermes chat -q "prompt" -Q` as a subprocess

### Token Optimization

Hermes has built-in Anthropic prompt caching (`system_and_3` strategy, 5-minute TTL). We also trim unused bundled skills and reduce tool definitions for non-code agents to cut input tokens.

---

## Troubleshooting

### Paperclip won't start
```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Check port isn't in use
lsof -i :3100

# WSL2: start PostgreSQL manually
sudo service postgresql start
```

### Agents not completing tasks
- Check agent status in Paperclip UI (http://localhost:3100)
- Check `~/.hermes/gladiator/{agent}/logs/errors.log` for API errors
- "Invalid API response after 3 retries" = Anthropic API rate limit or empty response (usually transient)
- Verify `.env` has valid `ANTHROPIC_API_KEY`

### Dashboard shows empty panels
- Evidence watcher must be running (auto-starts on LAUNCH DEMO)
- Check `/tmp/watcher.log` for errors
- Verify `evidence.db` exists and has data

### "adapter_failed" in Paperclip
- The `"NaN" bigint` error is cosmetic. Paperclip can't parse Hermes token output. Tasks still complete fine.

### Competition never ends
- Timer auto-stops at 600 seconds even if agents stall
- If dashboard was restarted mid-competition, in-memory state is lost. Use LAUNCH DEMO to restart

---

## Future Ideas

- **Espionage mechanic.** Agents browse each other's public GitHub and adapt strategy based on what the competitor shipped.
- **Spectator voting.** Dashboard lets visitors vote on which strategy they think will win, shown as a live poll.
- **ClipMart export.** Package both company configs as Paperclip ClipMart templates so anyone can import and run their own Gladiator match.

---

## Credits

Built by [RuntimeNotes Labs](https://github.com/runtimenoteslabs) for the Nous Research Hermes Agent Hackathon.

- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by Nous Research. Autonomous AI agent with persistent memory, skills and session continuity
- **[Paperclip](https://github.com/paperclipai/paperclip)** by Paperclip AI. AI company orchestration platform
- **Claude Sonnet** by Anthropic. Powers all 9 agents
