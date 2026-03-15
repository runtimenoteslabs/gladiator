// Gladiator Dashboard — Live SSE client with Gantt chart + Task Board + Code Comparison

let evtSource = null;
let competitionStartTime = null;

function startSSE() {
    evtSource = new EventSource('/api/stream');
    evtSource.addEventListener('state', (e) => {
        const state = JSON.parse(e.data);
        updateDashboard(state);
    });
    evtSource.addEventListener('error', () => {
        evtSource.close();
        setTimeout(startSSE, 5000);
    });
}

function updateDashboard(state) {
    // Check if competition is active
    const overlay = document.getElementById('no-competition-overlay');
    if (state.timer && state.timer.active === false) {
        if (overlay) overlay.style.display = 'flex';
        return;
    }
    if (overlay) overlay.style.display = 'none';

    // Timer
    document.getElementById('timer').textContent = state.timer.elapsed;
    document.getElementById('timer-remaining').textContent = state.timer.remaining;
    document.getElementById('progress').style.width = state.timer.progress_pct + '%';

    // Winner announcement
    if (state.finished && state.winner) {
        const w = state.winner;
        const winnerOverlay = document.getElementById('winner-overlay');
        if (winnerOverlay && !winnerOverlay.dataset.dismissed && winnerOverlay.style.display !== 'flex') {
            winnerOverlay.style.display = 'flex';
            const winColor = w.winner === 'Blitz' ? 'var(--blitz-primary)' : w.winner === 'Craft' ? 'var(--craft-primary)' : 'var(--gold)';
            winnerOverlay.innerHTML = `
                <div style="text-align:center;max-width:600px">
                    <div style="font-size:0.7rem;letter-spacing:4px;color:var(--text-dim);margin-bottom:8px">${w.reason === 'timer_expired' ? "TIME'S UP" : 'COMPETITION COMPLETE'}</div>
                    <div style="font-size:3rem;font-weight:bold;color:${winColor};letter-spacing:8px;margin-bottom:8px">${w.winner === 'Tie' ? 'TIE' : w.winner.toUpperCase() + ' WINS'}</div>
                    <div style="font-size:1.2rem;color:var(--text);margin-bottom:24px">${w.blitz_stars} vs ${w.craft_stars} projected stars &mdash; ${w.reason === 'timer_expired' ? 'decided at buzzer' : 'completed in ' + w.elapsed}</div>
                    <div style="display:flex;gap:12px;justify-content:center">
                        <button onclick="const o=document.getElementById('winner-overlay');o.style.display='none';o.dataset.dismissed='true'" style="padding:10px 24px;font-family:inherit;font-size:0.8rem;letter-spacing:2px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer">VIEW DASHBOARD</button>
                        <a href="/intel" style="padding:10px 24px;font-family:inherit;font-size:0.8rem;letter-spacing:2px;background:var(--surface2);color:var(--purple);border:1px solid var(--purple);border-radius:6px;cursor:pointer;text-decoration:none">INTEL REPORT</a>
                        <a href="/comparison" style="padding:10px 24px;font-family:inherit;font-size:0.8rem;letter-spacing:2px;background:var(--surface2);color:var(--gold);border:1px solid var(--gold);border-radius:6px;cursor:pointer;text-decoration:none">COMPARISON</a>
                    </div>
                    <div style="margin-top:16px;font-size:0.7rem;color:var(--text-dim)">Scroll down to MERGE the two companies</div>
                </div>
            `;
        }
    }

    if (!competitionStartTime && state.timer.elapsed !== '00:00:00') {
        competitionStartTime = Date.now() - parseTimer(state.timer.elapsed) * 1000;
    }

    updateCompany('blitz', state.blitz);
    updateCompany('craft', state.craft);
    updateMilestones(state.milestones);

    // Show merged state in merge section
    const mergeSection = document.getElementById('merge-section');
    if (state.merged && state.united) {
        const u = state.united;
        const b = state.blitz;
        const c = state.craft;
        const running = u.agents.filter(a => a.status === 'running').length;
        const tasks = u.issues || [];

        const taskHtml = tasks.map(t => {
            const statusColor = t.status === 'done' ? 'var(--green)' : t.status === 'todo' ? 'var(--gold)' : 'var(--blitz-primary)';
            return `<div style="font-size:0.78rem;padding:6px 0;border-bottom:1px solid var(--border);color:var(--text)">
                <span style="color:${statusColor};font-weight:bold;margin-right:8px">[${t.status.toUpperCase()}]</span>${t.title}
            </div>`;
        }).join('');

        const agentHtml = u.agents.map(a => {
            const color = a.status === 'running' ? 'var(--green)' : 'var(--text-dim)';
            const dot = a.status === 'running' ? '\u25CF ' : '\u25CB ';
            return `<div style="font-size:0.72rem;color:${color};padding:2px 0">${dot}${a.name}</div>`;
        }).join('');

        // Combined pre-merge stats
        const totalSkills = (b.skills_count || 0) + (c.skills_count || 0);
        const totalHb = (b.total_heartbeats || 0) + (c.total_heartbeats || 0);
        const totalCost = ((b.total_cost_usd || 0) + (c.total_cost_usd || 0)).toFixed(2);

        if (mergeSection) {
            document.getElementById('merge-status').innerHTML = `
                <div style="text-align:center;margin-bottom:20px">
                    <div style="font-size:1.5rem;font-weight:bold;color:var(--gold);letter-spacing:6px;margin-bottom:4px">GLADIATOR UNITED</div>
                    <div style="font-size:0.7rem;color:var(--text-dim);letter-spacing:2px">TWO RIVALS BECOME ONE TEAM</div>
                </div>

                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px">
                    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:16px">
                        <div style="font-size:0.6rem;font-weight:bold;letter-spacing:2px;color:var(--text-dim);margin-bottom:8px">COMBINED ASSETS</div>
                        <div style="font-size:0.78rem;color:var(--text);line-height:1.8">
                            <div>${u.agent_count} agents (${running} active)</div>
                            <div>10 tasks completed pre-merge</div>
                            <div>${totalSkills} skills merged</div>
                            <div>${totalHb} heartbeats total</div>
                            <div>$${totalCost} total cost</div>
                        </div>
                    </div>
                    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:16px">
                        <div style="font-size:0.6rem;font-weight:bold;letter-spacing:2px;color:var(--text-dim);margin-bottom:8px">POST-MERGE TASKS</div>
                        ${taskHtml || '<div style="font-size:0.75rem;color:var(--text-dim)">No post-merge tasks yet</div>'}
                        <div style="font-size:0.65rem;color:var(--craft-primary);margin-top:8px;font-style:italic">Cross-company skill transfer: Blitz engineer reviewing Craft's merged skills</div>
                    </div>
                    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:16px">
                        <div style="font-size:0.6rem;font-weight:bold;letter-spacing:2px;color:var(--text-dim);margin-bottom:8px">UNIFIED ROSTER</div>
                        ${agentHtml}
                    </div>
                </div>
            `;
        }
    }

    throttledFetchExtra();
}

function parseTimer(str) {
    const [h, m, s] = str.split(':').map(Number);
    return h * 3600 + m * 60 + s;
}

function updateCompany(key, data) {
    setText(`${key}-stars`, `\u2B50 ${data.stars || 0}`);
    setText(`${key}-cost`, '$' + (data.total_cost_usd || 0).toFixed(2));
    setText(`${key}-tasks`, `${data.issues_done || 0}/${data.issues_total || 0}`);
    setText(`${key}-skills`, data.skills_count || 0);
    setText(`${key}-heartbeats`, data.total_heartbeats || 0);

    const agentsEl = document.getElementById(`${key}-agents`);
    if (data.agents?.length) {
        agentsEl.innerHTML = data.agents.map(a => `
            <div class="agent-row">
                <span>${a.name} <span style="color:var(--text-dim);font-size:0.65rem">${a.model.split('-').slice(1, 2).join('')}</span></span>
                <span class="agent-status ${a.status}">${a.status.toUpperCase()}</span>
            </div>
        `).join('');
    }
}

function updateMilestones(milestones) {
    const el = document.getElementById('milestones-feed');
    if (!milestones?.length) {
        el.innerHTML = '<div class="empty-state">Agents haven\'t written skills or grown memory yet. This panel shows learning milestones as they happen.</div>';
        return;
    }

    el.innerHTML = milestones.map(m => {
        const time = m.created_at ? new Date(m.created_at + 'Z').toLocaleTimeString() : '';
        return `
            <div class="milestone">
                <span class="milestone-time">${time}</span>
                <span class="milestone-company ${m.company}">${(m.company || '').toUpperCase()}</span>
                <span class="milestone-badge ${m.milestone_type}">${fmtMilestone(m.milestone_type)}</span>
                <span>${esc(m.description)}</span>
            </div>
        `;
    }).join('');
}

// --- Gantt Chart ---

function updateGantt(activity) {
    const el = document.getElementById('gantt-chart');
    if (!activity?.length) {
        el.innerHTML = '<div class="empty-state">Waiting for agent activity...</div>';
        return;
    }

    // Sort by start time
    activity.sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));

    // Find time range
    const times = activity.flatMap(a => [a.start_time, a.end_time].filter(Boolean)).map(t => new Date(t).getTime());
    if (times.length === 0) {
        el.innerHTML = '<div class="empty-state">Waiting for agent activity...</div>';
        return;
    }

    const minTime = Math.min(...times);
    const maxTime = Math.max(...times, Date.now());
    const range = Math.max(maxTime - minTime, 60000); // at least 1 minute

    // Group by agent
    const agents = {};
    activity.forEach(a => {
        if (!agents[a.agent_name]) {
            agents[a.agent_name] = { company: a.company, tasks: [] };
        }
        agents[a.agent_name].tasks.push(a);
    });

    // Time axis labels
    const numMarks = 6;
    let timeAxis = '<div class="gantt-time-axis">';
    for (let i = 0; i < numMarks; i++) {
        const t = minTime + (range / (numMarks - 1)) * i;
        const d = new Date(t);
        timeAxis += `<span class="gantt-time-mark">${d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span>`;
    }
    timeAxis += '</div>';

    // Rows
    let rows = '';
    for (const [name, data] of Object.entries(agents)) {
        let bars = '';
        for (const task of data.tasks) {
            const start = new Date(task.start_time).getTime();
            const end = task.end_time ? new Date(task.end_time).getTime() : Date.now();
            const left = ((start - minTime) / range) * 100;
            const width = Math.max(((end - start) / range) * 100, 2);
            const isRunning = !task.end_time ? ' running' : '';
            const label = task.task_title || task.status || '';

            const tooltip = task.summary ? `${label}\n${task.summary}` : label;
            bars += `<div class="gantt-bar ${data.company}${isRunning}"
                style="left:${left}%;width:${width}%"
                title="${esc(tooltip)}">${esc(label)}</div>`;
        }

        const shortName = name.replace('Blitz ', '').replace('Craft ', '');
        const prefix = data.company === 'blitz' ? '\u26A1' : '\uD83D\uDD28';
        rows += `
            <div class="gantt-row">
                <div class="gantt-label">${prefix} ${shortName}</div>
                <div class="gantt-track">${bars}</div>
            </div>
        `;
    }

    el.innerHTML = timeAxis + rows;
}

// --- Skill Diary ---

function updateSkillDiary(company, skills) {
    const el = document.getElementById(`${company}-skill-diary`);
    if (!skills?.length) {
        el.innerHTML = '<div class="empty-state">No skills written yet. Skills appear when agents learn reusable patterns.</div>';
        return;
    }

    // Group by skill name, show versions
    const byName = {};
    skills.forEach(s => {
        if (!byName[s.skill_name]) byName[s.skill_name] = [];
        byName[s.skill_name].push(s);
    });

    el.innerHTML = Object.entries(byName).map(([name, versions]) => {
        const latest = versions[versions.length - 1];
        const versionBadges = versions.map(v =>
            `<span class="skill-version">v${v.version}</span>`
        ).join(' \u2192 ');

        let diffHtml = '';
        if (latest.diff_from_prev) {
            const lines = latest.diff_from_prev.split('\n').slice(0, 10);
            diffHtml = '<div class="skill-diff">' + lines.map(l => {
                if (l.startsWith('+') && !l.startsWith('+++')) return `<span class="diff-add">${esc(l)}</span>`;
                if (l.startsWith('-') && !l.startsWith('---')) return `<span class="diff-del">${esc(l)}</span>`;
                return esc(l);
            }).join('\n') + '</div>';
        }

        return `
            <div class="skill-card">
                <div class="skill-card-header">
                    <span class="skill-name">${esc(name)}</span>
                    ${versionBadges}
                </div>
                <div class="skill-agent">by ${esc(latest.agent_id)} &middot; ${versions.length} version(s)</div>
                ${diffHtml}
            </div>
        `;
    }).join('');
}

// --- Memory Growth ---

function updateMemoryGrowth(company, memoryData) {
    const el = document.getElementById(`${company}-memory`);
    if (!memoryData?.length) {
        el.innerHTML = '<div class="empty-state">Memory grows as agents learn facts about their environment and tasks.</div>';
        return;
    }

    // Group by agent, show latest char count
    const byAgent = {};
    memoryData.forEach(m => {
        if (m.memory_type !== 'memory') return;
        const key = m.agent_id;
        if (!byAgent[key] || m.heartbeat_num > byAgent[key].heartbeat_num) {
            byAgent[key] = m;
        }
    });

    const maxChars = 2200; // Hermes memory limit
    el.innerHTML = Object.entries(byAgent).map(([agent, data]) => {
        const pct = Math.min(100, (data.char_count / maxChars) * 100);
        const label = data.char_count > 0 ? `${data.char_count} / ${maxChars} chars` : 'empty';
        return `
            <div class="memory-agent-row">
                <div class="memory-agent-label">
                    <span>${agent}</span>
                    <span style="color:var(--text-dim)">${label}</span>
                </div>
                <div class="memory-bar-outer">
                    <div class="memory-bar-inner ${company}" style="width:${pct}%">${pct > 15 ? label : ''}</div>
                </div>
            </div>
        `;
    }).join('');
}

// --- Task Board ---

function updateTaskBoard(data) {
    for (const company of ['blitz', 'craft']) {
        const board = data[company];
        if (!board) continue;

        renderTaskColumn(`${company}-todo`, board.todo || []);
        renderTaskColumn(`${company}-in-progress`, board.in_progress || []);
        renderTaskColumn(`${company}-done`, board.done || []);
    }
}

function renderTaskColumn(elementId, issues) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (!issues.length) {
        el.innerHTML = '<div class="empty-state-small">None</div>';
        return;
    }

    el.innerHTML = issues.map(issue => {
        const priority = issue.priority || 'medium';
        const assignee = issue.assigneeAgentId ? `<span class="task-assignee">${esc(issue.assigneeAgentId.slice(0, 8))}</span>` : '';
        return `
            <div class="task-card priority-${priority}">
                <div class="task-card-title">${esc(issue.title)}</div>
                <div class="task-card-meta">
                    <span class="task-priority ${priority}">${priority.toUpperCase()}</span>
                    ${assignee}
                </div>
            </div>
        `;
    }).join('');
}

// --- Code Comparison ---

function updateCodeComparison(data) {
    for (const company of ['blitz', 'craft']) {
        const stats = data[company];
        if (!stats || stats.error) {
            document.getElementById(`${company}-code-stats`).innerHTML =
                `<div class="empty-state-small">${stats?.error || 'No repo found'}</div>`;
            continue;
        }

        // Stats summary
        document.getElementById(`${company}-code-stats`).innerHTML = `
            <div class="code-stat-row">
                <span class="code-stat-num">${stats.commits}</span>
                <span class="code-stat-label">commits</span>
                <span class="code-stat-num" style="margin-left:16px">${stats.files}</span>
                <span class="code-stat-label">files</span>
            </div>
        `;

        // Category bars
        const cats = stats.categories || {};
        const total = Object.values(cats).reduce((a, b) => a + b, 0) || 1;
        const catEl = document.getElementById(`${company}-categories`);
        catEl.innerHTML = `
            <div class="category-bars">
                ${renderCategoryBar('Features', cats.features || 0, total, 'var(--green)')}
                ${renderCategoryBar('Tests', cats.tests || 0, total, 'var(--blue)')}
                ${renderCategoryBar('Docs', cats.docs || 0, total, 'var(--gold)')}
                ${renderCategoryBar('Config', cats.config || 0, total, 'var(--text-dim)')}
            </div>
        `;

        // Recent commits
        const commitEl = document.getElementById(`${company}-commits`);
        if (stats.recent_commits?.length) {
            commitEl.innerHTML = `
                <div class="recent-commits-label">RECENT COMMITS</div>
                ${stats.recent_commits.map(c => `<div class="commit-line">${esc(c)}</div>`).join('')}
            `;
        }
    }
}

function renderCategoryBar(label, count, total, color) {
    const pct = Math.round((count / total) * 100);
    return `
        <div class="category-row">
            <span class="category-label">${label}</span>
            <div class="category-bar-outer">
                <div class="category-bar-inner" style="width:${pct}%;background:${color}"></div>
            </div>
            <span class="category-count">${count} (${pct}%)</span>
        </div>
    `;
}

// --- Throttled fetch for extra data ---

let lastExtraFetch = 0;

async function throttledFetchExtra() {
    const now = Date.now();
    if (now - lastExtraFetch < 10000) return;
    lastExtraFetch = now;

    try {
        const [learning, activity, audit, taskboard, codeComparison] = await Promise.allSettled([
            fetch('/api/learning').then(r => r.json()),
            fetch('/api/gantt').then(r => r.json()),
            fetch('/api/audit').then(r => r.json()),
            fetch('/api/taskboard').then(r => r.json()),
            fetch('/api/code-comparison').then(r => r.json()),
        ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null));

        if (activity) updateGantt(activity);
        if (audit) { updateAudit('blitz', audit.blitz || []); updateAudit('craft', audit.craft || []); }
        if (taskboard) updateTaskBoard(taskboard);
        if (codeComparison) updateCodeComparison(codeComparison);

        // Skill diaries
        const blitzSkills = (learning.milestones || []).filter(m =>
            m.company === 'blitz' && (m.milestone_type === 'skill_created' || m.milestone_type === 'skill_improved')
        );
        const craftSkills = (learning.milestones || []).filter(m =>
            m.company === 'craft' && (m.milestone_type === 'skill_created' || m.milestone_type === 'skill_improved')
        );

        // Fetch actual skill snapshots
        const [blitzSkillData, craftSkillData] = await Promise.all([
            fetch('/api/skills/blitz').then(r => r.json()),
            fetch('/api/skills/craft').then(r => r.json()),
        ]);
        updateSkillDiary('blitz', blitzSkillData);
        updateSkillDiary('craft', craftSkillData);

        // Memory growth
        const [blitzMem, craftMem] = await Promise.all([
            fetch('/api/memory-by-company/blitz').then(r => r.json()),
            fetch('/api/memory-by-company/craft').then(r => r.json()),
        ]);
        updateMemoryGrowth('blitz', blitzMem);
        updateMemoryGrowth('craft', craftMem);

    } catch (e) {
        console.error('Extra fetch error:', e);
    }
}

// --- Intel Report ---

function updateInsights(data) {
    const summaryEl = document.getElementById('intel-summary');
    if (summaryEl && data.summary) {
        summaryEl.textContent = data.summary;
    }

    const checksEl = document.getElementById('intel-checks');
    if (checksEl && data.checks?.length) {
        checksEl.innerHTML = data.checks.map(c => {
            const icon = c.status === 'pass' ? '\u2705' : c.status === 'warn' ? '\u26A0\uFE0F' : '\u274C';
            const color = c.status === 'pass' ? 'var(--green)' : c.status === 'warn' ? 'var(--gold)' : '#ff4444';
            return `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--border);font-size:0.7rem">
                <span>${icon}</span>
                <span style="font-weight:bold;color:${color};min-width:100px">${c.name}</span>
                <span style="color:var(--text-dim)">${c.detail}</span>
            </div>`;
        }).join('');
    }

    const insightsEl = document.getElementById('intel-insights');
    if (insightsEl && data.insights?.length) {
        const typeColors = {pace:'var(--blitz-primary)', divergence:'var(--gold)', learning:'var(--craft-primary)', strategy:'var(--purple)'};
        insightsEl.innerHTML = data.insights.map(i => {
            const color = typeColors[i.type] || 'var(--text-dim)';
            return `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:0.75rem">
                <span style="display:inline-block;font-size:0.55rem;font-weight:bold;letter-spacing:1px;padding:2px 6px;border-radius:3px;margin-right:8px;background:var(--surface2);color:${color}">${i.type.toUpperCase()}</span>
                <span style="color:var(--text)">${i.text}</span>
            </div>`;
        }).join('');
    } else if (insightsEl) {
        insightsEl.innerHTML = '<div style="font-size:0.7rem;color:var(--text-dim)">Waiting for agent activity...</div>';
    }
}

// --- Audit Trail ---

function updateAudit(company, items) {
    const el = document.getElementById(`${company}-audit`);
    if (!items?.length) {
        el.innerHTML = '<div class="empty-state">No actions yet</div>';
        return;
    }

    el.innerHTML = items.map(item => {
        const icon = item.type === 'commit' ? '\uD83D\uDCBB' : '\u2705';
        const typeLabel = item.type === 'commit' ? 'COMMITTED' : 'COMPLETED';
        const time = item.timestamp ? new Date(item.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
        return `
            <div class="milestone">
                <span class="milestone-time">${time}</span>
                <span class="milestone-badge ${item.type === 'commit' ? 'skill_created' : 'efficiency_gain'}">${icon} ${typeLabel}</span>
                <span style="font-size:0.7rem;color:var(--text-dim)">${esc(item.agent)}</span>
                <div style="margin-top:3px">${esc(item.title)}</div>
            </div>
        `;
    }).join('');
}

// --- Utilities ---

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function esc(text) {
    if (!text) return '';
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function fmtMilestone(type) {
    const labels = {
        'skill_created': 'NEW SKILL',
        'skill_improved': 'SKILL IMPROVED',
        'skill_used': 'SKILL USED',
        'memory_growth': 'MEMORY GREW',
        'efficiency_gain': 'MORE EFFICIENT',
        'cross_agent_learning': 'CROSS-AGENT',
    };
    return labels[type] || type;
}

// --- Merge / Unmerge / Reset ---

async function doMerge() {
    const btn = document.getElementById('merge-btn');
    const output = document.getElementById('merge-output');
    btn.disabled = true;
    btn.textContent = 'MERGING...';
    output.textContent = 'Running merge script...';

    try {
        const resp = await fetch('/api/merge', {method: 'POST'});
        const data = await resp.json();
        if (data.status === 'merged') {
            let msg = '<span style="color:var(--green)">MERGED SUCCESSFULLY!</span><br>';
            msg += data.output.split('\n').filter(l => l.trim()).slice(-5).join('<br>');
            if (data.post_merge) {
                msg += '<br><br><span style="color:var(--gold)">POST-MERGE TASK:</span> ' + esc(data.post_merge);
                msg += '<br><span style="color:var(--text-dim)">A former Blitz agent is now using Craft\'s skills. Watch the activity timeline for proof of cross-company skill transfer.</span>';
            }
            output.innerHTML = msg;
            btn.style.display = 'none';
            document.getElementById('unmerge-btn').style.display = 'inline-block';
        } else {
            output.innerHTML = '<span style="color:var(--red)">MERGE FAILED</span><br>' + (data.error || data.output);
            btn.disabled = false;
            btn.textContent = 'MERGE COMPANIES';
        }
    } catch(e) {
        output.textContent = 'Error: ' + e.message;
        btn.disabled = false;
        btn.textContent = 'MERGE COMPANIES';
    }
}

async function doUnmerge() {
    const output = document.getElementById('merge-output');
    output.textContent = 'Restoring original companies...';
    try {
        const resp = await fetch('/api/unmerge', {method: 'POST'});
        const data = await resp.json();
        output.innerHTML = '<span style="color:var(--green)">RESTORED!</span> Blitz and Craft are back.';
        document.getElementById('merge-btn').style.display = 'inline-block';
        document.getElementById('merge-btn').disabled = false;
        document.getElementById('merge-btn').textContent = 'MERGE COMPANIES';
        document.getElementById('unmerge-btn').style.display = 'none';
    } catch(e) {
        output.textContent = 'Error: ' + e.message;
    }
}

async function doReset() {
    if (!confirm('Reset the demo? Timer will restart.')) return;
    try {
        await fetch('/api/reset-demo', {method: 'POST'});
        window.location.href = '/landing';
    } catch(e) {
        alert('Reset error: ' + e.message);
    }
}

// Start
startSSE();
