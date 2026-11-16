(() => {
  const DEMO_LEDGER = {
    namespace: "demo-preview",
    captured_at: "2026-11-16T06:20:00.000000+00:00",
    known_at: "2026-11-16T06:20:00.000000+00:00",
    at: "2026-11-16T06:20:00.000000+00:00",
    total_versions: 5,
    visible_versions: 5,
    logical_count: 4,
    checkpoints: [
      "2026-10-18T11:30:00.000000+00:00",
      "2026-10-19T02:00:00.000000+00:00",
      "2026-11-12T09:00:00.000000+00:00",
      "2026-11-15T14:20:00.000000+00:00",
      "2026-11-16T05:10:00.000000+00:00"
    ],
    facets: {
      workspaces: ["/Users/d/D/EarnByAi/chronicle-memory", "/Users/d/projects/aurora"],
      origins: ["codex", "claude"],
      conversations: ["codex:session-101", "claude:arch-review"],
      entities: [
        { id: "aurora-review", name: "极光评审", category: "project", aliases: ["北辰碰头", "Aurora Review"], count: 2 },
        { id: "friend-lin", name: "小林", category: "person", aliases: ["Lin"], count: 2 },
        { id: "sqlite-fts5", name: "SQLite FTS5", category: "tech", aliases: ["本地全文检索"], count: 1 }
      ]
    },
    memories: [
      {
        seq: 5,
        record_id: "rec-aurora-v2",
        logical_id: "aurora-meeting",
        record_kind: "experience",
        title: "极光评审会议地点变更",
        summary: "更正：极光评审（北辰碰头）实际改在五楼 B502 会议室举行，不再使用三楼会议室。",
        narrative: "更正：极光评审（北辰碰头）实际改在五楼 B502 会议室举行，不再使用三楼会议室。",
        when: { precision: "day", start: "2026-11-16T09:00:00+00:00", description: "2026-11-16 上午" },
        basis: null,
        supports: [],
        entities: [
          { id: "aurora-review", name: "极光评审", category: "project", aliases: ["北辰碰头"] },
          { id: "friend-lin", name: "小林", category: "person", aliases: ["Lin"] }
        ],
        replaces: "rec-aurora-v1",
        revision_kind: "correct",
        recorded_at: "2026-11-16T05:10:00.000000+00:00",
        effective_at: "2026-11-16T09:00:00.000000+00:00",
        effective: true,
        origin: "codex",
        workspace: "/Users/d/projects/aurora",
        conversation_id: "codex:session-101",
        source_id: "msg-102",
        evidence_ids: ["msg-102", "msg-101"],
        version_count: 2,
        has_correction: true,
        needs_review: false,
        sources: [
          { id: "msg-102", role: "user", occurred_at: "2026-11-16T05:09:50+00:00", conversation_id: "codex:session-101", content: "刚收到小林通知，极光评审（北辰碰头）改到五楼 B502 会议室了，之前说的三楼作废。" },
          { id: "msg-101", role: "user", occurred_at: "2026-11-12T08:59:00+00:00", conversation_id: "codex:session-101", content: "下周一和林哥的极光评审定在三楼会议室。" }
        ],
        revisions: [
          {
            version_number: 1,
            record_id: "rec-aurora-v1",
            title: "极光评审会议安排",
            narrative: "极光评审定于三楼会议室举行，与小林一同参会。",
            revision_kind: "update",
            recorded_at: "2026-11-12T09:00:00.000000+00:00",
            source_id: "msg-101",
            superseded: true
          },
          {
            version_number: 2,
            record_id: "rec-aurora-v2",
            title: "极光评审会议地点变更",
            narrative: "更正：极光评审（北辰碰头）实际改在五楼 B502 会议室举行，不再使用三楼会议室。",
            revision_kind: "correct",
            recorded_at: "2026-11-16T05:10:00.000000+00:00",
            source_id: "msg-102",
            superseded: false
          }
        ],
        neighbors: [
          { logical_id: "aurora-prep-insight", record_id: "rec-insight-1", title: "提前去三楼调试投影仪", record_kind: "understanding", reasons: [{ kind: "supported_by", label: "衍生认识" }, { kind: "shared_entity", label: "共同实体: 极光评审" }] },
          { logical_id: "camping-with-lin", record_id: "rec-camp-1", title: "与小林秋季露营遇雨", record_kind: "experience", reasons: [{ kind: "shared_entity", label: "共同实体: 小林" }] }
        ]
      },
      {
        seq: 4,
        record_id: "rec-arch-1",
        logical_id: "sqlite-backend-decision",
        record_kind: "understanding",
        title: "Chronicle 存储后端收敛为单文件 SQLite + FTS5",
        summary: "用户明确决定移除 Neo4j 与 Java 依赖，以 SQLite + FTS5 作为唯一存储后端，确保 MCP 零外部服务即可启动。",
        narrative: "用户明确决定移除 Neo4j 与 Java 依赖，以 SQLite + FTS5 作为唯一存储后端，确保 MCP 零外部服务即可启动。",
        when: { precision: "exact", start: "2026-11-15T14:20:00+00:00", description: "2026-11-15 架构重构" },
        basis: "explicit",
        supports: ["rec-camp-1"],
        entities: [
          { id: "sqlite-fts5", name: "SQLite FTS5", category: "tech", aliases: ["本地全文检索"] }
        ],
        replaces: null,
        revision_kind: "update",
        recorded_at: "2026-11-15T14:20:00.000000+00:00",
        effective_at: "2026-11-15T14:20:00.000000+00:00",
        effective: true,
        origin: "claude",
        workspace: "/Users/d/D/EarnByAi/chronicle-memory",
        conversation_id: "claude:arch-review",
        source_id: "msg-arch-01",
        evidence_ids: ["msg-arch-01"],
        version_count: 1,
        has_correction: false,
        needs_review: false,
        sources: [
          { id: "msg-arch-01", role: "user", occurred_at: "2026-11-15T14:19:10+00:00", conversation_id: "claude:arch-review", content: "把 Neo4j 彻底去掉吧，只保留 SQLite + FTS5，让用户装完 Python 包直接就能跑 MCP。" }
        ],
        revisions: [
          {
            version_number: 1,
            record_id: "rec-arch-1",
            title: "Chronicle 存储后端收敛为单文件 SQLite + FTS5",
            narrative: "用户明确决定移除 Neo4j 与 Java 依赖，以 SQLite + FTS5 作为唯一存储后端，确保 MCP 零外部服务即可启动。",
            revision_kind: "update",
            recorded_at: "2026-11-15T14:20:00.000000+00:00",
            source_id: "msg-arch-01",
            superseded: false
          }
        ],
        neighbors: []
      },
      {
        seq: 3,
        record_id: "rec-insight-1",
        logical_id: "aurora-prep-insight",
        record_kind: "understanding",
        title: "评审前需提前到三楼测试转接头",
        summary: "基于三楼会议室旧投影仪的安排，推测用户需要在极光评审前 15 分钟去三楼调试转接头。",
        narrative: "基于三楼会议室旧投影仪的安排，推测用户需要在极光评审前 15 分钟去三楼调试转接头。",
        when: { precision: "unknown", description: "极光评审会前" },
        basis: "inferred",
        supports: ["rec-aurora-v1"],
        entities: [
          { id: "aurora-review", name: "极光评审", category: "project", aliases: ["北辰碰头"] }
        ],
        replaces: null,
        revision_kind: "update",
        recorded_at: "2026-11-12T09:05:00.000000+00:00",
        effective_at: null,
        effective: true,
        origin: "codex",
        workspace: "/Users/d/projects/aurora",
        conversation_id: "codex:session-101",
        source_id: "msg-101",
        evidence_ids: ["msg-101"],
        version_count: 1,
        has_correction: false,
        needs_review: true,
        sources: [
          { id: "msg-101", role: "user", occurred_at: "2026-11-12T08:59:00+00:00", conversation_id: "codex:session-101", content: "下周一和林哥的极光评审定在三楼会议室。" }
        ],
        revisions: [
          {
            version_number: 1,
            record_id: "rec-insight-1",
            title: "评审前需提前到三楼测试转接头",
            narrative: "基于三楼会议室旧投影仪的安排，推测用户需要在极光评审前 15 分钟去三楼调试转接头。",
            revision_kind: "update",
            recorded_at: "2026-11-12T09:05:00.000000+00:00",
            source_id: "msg-101",
            superseded: false
          }
        ],
        neighbors: [
          { logical_id: "aurora-meeting", record_id: "rec-aurora-v2", title: "极光评审会议地点变更", record_kind: "experience", reasons: [{ kind: "supports", label: "支撑经历 (已更正)" }] }
        ]
      },
      {
        seq: 1,
        record_id: "rec-camp-1",
        logical_id: "camping-with-lin",
        record_kind: "experience",
        title: "与小林秋季露营遇雨",
        summary: "10 月与小林去山谷露营时突遇大雨提前返程，但两人分工收拾装备非常默契。",
        narrative: "10 月与小林去山谷露营时突遇大雨提前返程，但两人分工收拾装备非常默契。",
        when: { precision: "approximate", description: "2026 年 10 月中旬，具体日期不详" },
        basis: null,
        supports: [],
        entities: [
          { id: "friend-lin", name: "小林", category: "person", aliases: ["Lin"] }
        ],
        replaces: null,
        revision_kind: "update",
        recorded_at: "2026-10-18T11:30:00.000000+00:00",
        effective_at: null,
        effective: true,
        origin: "codex",
        workspace: "/Users/d/projects/aurora",
        conversation_id: "codex:session-101",
        source_id: "msg-050",
        evidence_ids: ["msg-050"],
        version_count: 1,
        has_correction: false,
        needs_review: false,
        sources: [
          { id: "msg-050", role: "user", occurred_at: "2026-10-18T11:29:00+00:00", conversation_id: "codex:session-101", content: "上周末我和小林去露营遇上大雨提前回来了，不过我们俩配合收拾帐篷特别默契。" }
        ],
        revisions: [
          {
            version_number: 1,
            record_id: "rec-camp-1",
            title: "与小林秋季露营遇雨",
            narrative: "10 月与小林去山谷露营时突遇大雨提前返程，但两人分工收拾装备非常默契。",
            revision_kind: "update",
            recorded_at: "2026-10-18T11:30:00.000000+00:00",
            source_id: "msg-050",
            superseded: false
          }
        ],
        neighbors: [
          { logical_id: "aurora-meeting", record_id: "rec-aurora-v2", title: "极光评审会议地点变更", record_kind: "experience", reasons: [{ kind: "shared_entity", label: "共同实体: 小林" }] }
        ]
      }
    ]
  };

  const state = {
    raw: null,
    useDemo: false,
    checkpoints: [],
    selectedCheckpointIdx: null,
    kindFilter: "all",
    workspaceFilter: "",
    conversationFilter: "",
    entityFilter: "",
    searchQuery: "",
    selectedLogicalId: null
  };

  const el = (id) => document.getElementById(id);

  function fmtTime(iso) {
    if (!iso) return "未指定";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadLedger(knownAt = null) {
    if (state.useDemo) {
      applySnapshot(projectDemoAt(knownAt));
      return;
    }
    const url = knownAt
      ? `/api/ledger?${new URLSearchParams({ known_at: knownAt, at: knownAt })}`
      : "/api/ledger";
    try {
      const res = await fetch(url, { headers: { Accept: "application/json" } });
      const payload = await res.json();
      if (!payload.ok) throw new Error(payload.error?.message || "加载失败");
      if (payload.result.total_versions === 0 && !knownAt && state.checkpoints.length === 0) {
        // Automatically enable demo preview when database is brand new/empty so user can see the design immediately
        state.useDemo = true;
        el("btn-demo-toggle").classList.add("active-demo");
        applySnapshot(projectDemoAt(null), true);
        return;
      }
      applySnapshot(payload.result, false);
    } catch (err) {
      el("stream-status").textContent = `读取失败：${err.message}`;
    }
  }

  function projectDemoAt(knownAt) {
    if (!knownAt) return structuredClone(DEMO_LEDGER);
    const clone = structuredClone(DEMO_LEDGER);
    clone.known_at = knownAt;
    const filtered = [];
    for (const m of clone.memories) {
      const revs = m.revisions.filter((r) => r.recorded_at <= knownAt);
      if (!revs.length) continue;
      const head = revs[revs.length - 1];
      filtered.push({
        ...m,
        record_id: head.record_id,
        title: head.title,
        summary: head.narrative,
        narrative: head.narrative,
        recorded_at: head.recorded_at,
        revision_kind: head.revision_kind,
        version_count: revs.length,
        has_correction: revs.slice(1).some((r) => r.revision_kind === "correct"),
        needs_review: m.logical_id === "aurora-prep-insight" && revs.length > 0 && knownAt >= "2026-11-16T05:10:00.000000+00:00",
        revisions: revs.map((r, i) => ({ ...r, superseded: i < revs.length - 1 }))
      });
    }
    clone.memories = filtered;
    clone.visible_versions = filtered.reduce((acc, m) => acc + m.version_count, 0);
    clone.logical_count = filtered.length;
    return clone;
  }

  function applySnapshot(snapshot, autoSwitchedDemo = false) {
    state.raw = snapshot;
    el("ns-badge").textContent = state.useDemo
      ? `预览模式 (示例数据)`
      : `namespace: ${snapshot.namespace}`;

    // Update slider checkpoints on initial load
    if (snapshot.checkpoints && snapshot.checkpoints.length > 0) {
      state.checkpoints = snapshot.checkpoints;
      const slider = el("tm-slider");
      slider.max = String(state.checkpoints.length - 1);
      if (state.selectedCheckpointIdx === null || state.selectedCheckpointIdx >= state.checkpoints.length) {
        state.selectedCheckpointIdx = state.checkpoints.length - 1;
      }
      slider.value = String(state.selectedCheckpointIdx);
      slider.disabled = false;
    } else {
      const slider = el("tm-slider");
      slider.max = "0";
      slider.value = "0";
      slider.disabled = true;
    }

    const isTimeTraveling =
      state.checkpoints.length > 0 &&
      state.selectedCheckpointIdx !== null &&
      state.selectedCheckpointIdx < state.checkpoints.length - 1;
    el("tm-reset").hidden = !isTimeTraveling;
    el("tm-label").textContent = isTimeTraveling
      ? `历史视角: ${fmtTime(snapshot.known_at)}`
      : "当前视角 (Now)";

    populateScopeSelects(snapshot.facets);
    renderEntities(snapshot.facets.entities);
    renderStream(autoSwitchedDemo);
  }

  function populateScopeSelects(facets) {
    const wsSelect = el("select-workspace");
    const convSelect = el("select-conversation");
    const keepWs = state.workspaceFilter;
    const keepConv = state.conversationFilter;

    wsSelect.innerHTML = `<option value="">全部工作区 (${facets.workspaces.length})</option>` +
      facets.workspaces.map((w) => `<option value="${escapeHtml(w)}">${escapeHtml(w)}</option>`).join("");
    wsSelect.value = keepWs;

    convSelect.innerHTML = `<option value="">全部会话 (${facets.conversations.length})</option>` +
      facets.conversations.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    convSelect.value = keepConv;

    el("reset-scope").hidden = !(state.workspaceFilter || state.conversationFilter);
  }

  function renderEntities(entities) {
    const container = el("entity-list");
    el("reset-entity").hidden = !state.entityFilter;
    if (!entities || !entities.length) {
      container.innerHTML = `<div class="entity-aliases" style="padding:6px;">暂无关联实体</div>`;
      return;
    }
    container.innerHTML = entities
      .map((ent) => {
        const active = state.entityFilter === ent.id ? "active" : "";
        const aliasText = ent.aliases && ent.aliases.length ? `别名: ${ent.aliases.join(" / ")}` : ent.category;
        return `
          <button type="button" class="entity-chip ${active}" data-entity="${escapeHtml(ent.id)}">
            <div class="entity-meta">
              <span class="entity-name">${escapeHtml(ent.name)}</span>
              <span class="entity-aliases">${escapeHtml(aliasText)}</span>
            </div>
            <span class="count-pill">${ent.count}</span>
          </button>
        `;
      })
      .join("");

    container.querySelectorAll(".entity-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-entity");
        state.entityFilter = state.entityFilter === id ? "" : id;
        renderEntities(state.raw.facets.entities);
        renderStream();
      });
    });
  }

  function filterMemories(memories) {
    const q = state.searchQuery.trim().toLowerCase();
    return memories.filter((m) => {
      if (state.kindFilter === "experience" && m.record_kind !== "experience") return false;
      if (state.kindFilter === "understanding" && m.record_kind !== "understanding") return false;
      if (state.kindFilter === "revised" && m.version_count < 2) return false;
      if (state.kindFilter === "needs_review" && !m.needs_review) return false;
      if (state.workspaceFilter && m.workspace !== state.workspaceFilter) return false;
      if (state.conversationFilter && m.conversation_id !== state.conversationFilter) return false;
      if (state.entityFilter && !(m.entities || []).some((e) => e.id === state.entityFilter)) return false;
      if (q) {
        const hay = [
          m.title,
          m.narrative,
          m.logical_id,
          ...(m.entities || []).flatMap((e) => [e.id, e.name, ...(e.aliases || [])]),
          ...(m.sources || []).map((s) => s.content)
        ]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function updateCounters(memories) {
    el("count-all").textContent = memories.length;
    el("count-experience").textContent = memories.filter((m) => m.record_kind === "experience").length;
    el("count-understanding").textContent = memories.filter((m) => m.record_kind === "understanding").length;
    el("count-revised").textContent = memories.filter((m) => m.version_count > 1).length;
    el("count-review").textContent = memories.filter((m) => m.needs_review).length;
  }

  function renderStream(autoSwitchedDemo = false) {
    const all = state.raw?.memories || [];
    updateCounters(all);
    const filtered = filterMemories(all);

    const statusPrefix = autoSwitchedDemo
      ? "当前 SQLite 为空，已自动开启【示例预览】展示三栏设计 · "
      : state.useDemo
      ? "【示例预览模式】 · "
      : "";
    el("stream-status").textContent = `${statusPrefix}共展示 ${filtered.length} / ${all.length} 条逻辑记忆（累计 ${state.raw?.visible_versions || 0} 个版本）`;

    const listEl = el("memory-list");
    if (!filtered.length) {
      listEl.innerHTML = `
        <div class="inspector-empty" style="height:260px;">
          <p class="empty-title">没有匹配的记忆记录</p>
          <p class="empty-sub">尝试调整左侧筛选条件、搜索词，或将顶部时间机器滑轨拨回最新时刻。</p>
        </div>
      `;
      renderInspector(null);
      return;
    }

    if (!state.selectedLogicalId || !filtered.some((m) => m.logical_id === state.selectedLogicalId)) {
      state.selectedLogicalId = filtered[0].logical_id;
    }

    listEl.innerHTML = filtered
      .map((m) => {
        const selected = m.logical_id === state.selectedLogicalId ? "selected" : "";
        const kindClass = `kind-${m.record_kind} ${m.basis ? `basis-${m.basis}` : ""}`;
        let kindBadge = `<span class="badge badge-exp">经历</span>`;
        if (m.record_kind === "understanding") {
          kindBadge =
            m.basis === "explicit"
              ? `<span class="badge badge-explicit">用户明确表达</span>`
              : `<span class="badge badge-inferred">AI 归纳推测</span>`;
        }
        const revBadge =
          m.version_count > 1
            ? `<span class="badge ${m.has_correction ? "badge-correct" : "badge-rev"}">v${m.version_count} · ${
                m.has_correction ? "已纠错" : "已补充"
              }</span>`
            : "";
        const reviewBadge = m.needs_review
          ? `<span class="badge badge-review">需重新审视 (needs_review)</span>`
          : "";
        const validityLabel = m.expired ? "已到期" : m.effective ? "有效" : "尚未生效";
        const validityBadge = m.valid_to
          ? `<span class="badge ${m.effective ? "badge-exp" : "badge-review"}">${validityLabel}</span>`
          : "";
        const whenDesc =
          m.when?.description ||
          (m.when?.start ? fmtTime(m.when.start) : `获知于 ${fmtTime(m.recorded_at)}`);
        const entityTags = (m.entities || [])
          .map((e) => `<span class="tag-entity">@${escapeHtml(e.name)}</span>`)
          .join("");

        return `
          <article class="memory-card ${kindClass} ${selected}" data-logical="${escapeHtml(m.logical_id)}" role="option">
            <div class="card-top">
              <div class="card-title-row">
                ${kindBadge}
                <h3 class="card-title">${escapeHtml(m.title)}</h3>
                ${revBadge}
                ${reviewBadge}
                ${validityBadge}
              </div>
            </div>
            <p class="card-narrative">${escapeHtml(m.summary)}</p>
            <div class="card-footer">
              <div class="card-tags">${entityTags}</div>
              <span class="card-time">${escapeHtml(whenDesc)}</span>
            </div>
          </article>
        `;
      })
      .join("");

    listEl.querySelectorAll(".memory-card").forEach((card) => {
      card.addEventListener("click", () => {
        state.selectedLogicalId = card.getAttribute("data-logical");
        renderStream();
      });
    });

    const activeMem = filtered.find((m) => m.logical_id === state.selectedLogicalId) || filtered[0];
    renderInspector(activeMem);
  }

  function buildEgoSvg(mem) {
    const sources = mem.sources || [];
    const neighbors = mem.neighbors || [];
    const leftCount = Math.max(1, sources.length);
    const rightCount = Math.max(1, neighbors.length);
    const rows = Math.max(leftCount, rightCount, 1);
    const height = Math.max(120, rows * 44 + 24);
    const cy = Math.round(height / 2);

    let svg = `<svg viewBox="0 0 380 ${height}" width="100%" height="${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:var(--font-sans);font-size:11px;">`;

    // Left: Sources
    sources.forEach((src, i) => {
      const sy = Math.round(((i + 1) * height) / (sources.length + 1));
      svg += `<line x1="104" y1="${sy}" x2="142" y2="${cy}" stroke="var(--border-strong)" stroke-width="1.3" stroke-dasharray="3 3"/>`;
      svg += `<rect x="8" y="${sy - 14}" width="96" height="28" rx="14" fill="var(--bg-surface)" stroke="var(--border-strong)"/>`;
      svg += `<text x="56" y="${sy}" text-anchor="middle" dominant-baseline="central" fill="var(--text-secondary)">来源: ${escapeHtml(src.id.slice(0, 10))}</text>`;
    });

    // Center: Current Memory
    svg += `<rect x="142" y="${cy - 18}" width="96" height="36" rx="18" fill="var(--bg-active)" stroke="var(--accent)" stroke-width="1.5"/>`;
    svg += `<text x="190" y="${cy}" text-anchor="middle" dominant-baseline="central" fill="var(--accent)" font-weight="600">${escapeHtml(mem.title.slice(0, 7))}</text>`;

    // Right: 1-Hop Neighbors
    if (neighbors.length) {
      neighbors.slice(0, 4).forEach((nb, i) => {
        const ny = Math.round(((i + 1) * height) / (Math.min(4, neighbors.length) + 1));
        svg += `<line x1="238" y1="${cy}" x2="274" y2="${ny}" stroke="var(--border-strong)" stroke-width="1.3" stroke-dasharray="3 3"/>`;
        svg += `<rect x="274" y="${ny - 14}" width="98" height="28" rx="14" fill="var(--bg-surface)" stroke="var(--border-strong)"/>`;
        svg += `<text x="323" y="${ny}" text-anchor="middle" dominant-baseline="central" fill="var(--text-secondary)">${escapeHtml(nb.title.slice(0, 7))}</text>`;
      });
    } else {
      svg += `<text x="316" y="${cy}" text-anchor="middle" dominant-baseline="central" fill="var(--text-muted)">无相邻记忆</text>`;
    }

    svg += `</svg>`;
    return svg;
  }

  function renderInspector(mem) {
    const emptyEl = el("inspector-empty");
    const contentEl = el("inspector-content");
    if (!mem) {
      emptyEl.hidden = false;
      contentEl.hidden = true;
      return;
    }
    emptyEl.hidden = true;
    contentEl.hidden = false;

    const revisionsHtml = (mem.revisions || [])
      .slice()
      .reverse()
      .map((rev, idx, arr) => {
        const prev = arr[idx + 1];
        const isHead = !rev.superseded;
        const diffHtml = prev
          ? `<div class="diff-box">
               <div class="diff-old">- ${escapeHtml(prev.narrative)}</div>
               <div class="diff-new">+ ${escapeHtml(rev.narrative)}</div>
             </div>`
          : `<div class="card-narrative" style="-webkit-line-clamp:unset;">${escapeHtml(rev.narrative)}</div>`;

        return `
          <div class="rev-item ${isHead ? "rev-head" : ""}">
            <div class="rev-top">
              <span class="rev-ver">v${rev.version_number} · ${rev.revision_kind === "correct" ? "纠错 (correct)" : "记录 (update)"} ${isHead ? "· 当前版本" : "· 历史版本"}</span>
              <span class="card-time">${fmtTime(rev.recorded_at)}</span>
            </div>
            ${diffHtml}
          </div>
        `;
      })
      .join("");

    const sourcesHtml = (mem.sources || [])
      .map(
        (s) => `
        <div class="source-card">
          <div class="source-header">
            <span>source.id: ${escapeHtml(s.id)} (${escapeHtml(s.role)})</span>
            <span>${fmtTime(s.occurred_at)}</span>
          </div>
          <blockquote class="source-quote">${escapeHtml(s.content)}</blockquote>
        </div>
      `
      )
      .join("");

    const neighborsHtml = (mem.neighbors || []).length
      ? mem.neighbors
          .map(
            (nb) => `
          <button type="button" class="neighbor-btn" data-jump="${escapeHtml(nb.logical_id)}">
            <div>
              <div style="font-weight:600;">${escapeHtml(nb.title)}</div>
              <div class="neighbor-reason">${escapeHtml(nb.reasons.map((r) => r.label).join(" · "))}</div>
            </div>
            <span class="badge badge-exp">跳转 →</span>
          </button>
        `
          )
          .join("")
      : `<div class="entity-aliases">当前视角下暂无共享实体或支撑关联的邻居记忆</div>`;

    contentEl.innerHTML = `
      <div class="insp-header">
        <div class="card-title-row">
          <h2 class="insp-title">${escapeHtml(mem.title)}</h2>
        </div>
        <div class="insp-id">logical_id: ${escapeHtml(mem.logical_id)} · record_id: ${escapeHtml(mem.record_id)}</div>
      </div>

      <div class="insp-meta-grid">
        <div class="meta-cell">
          <span class="meta-label">发生时间 (when)</span>
          <span class="meta-val">${escapeHtml(mem.when?.description || fmtTime(mem.when?.start) || "未知")} (${escapeHtml(mem.when?.precision || "unknown")})</span>
        </div>
        <div class="meta-cell">
          <span class="meta-label">获知时间 (recorded_at)</span>
          <span class="meta-val">${fmtTime(mem.recorded_at)}</span>
        </div>
        <div class="meta-cell">
          <span class="meta-label">工作区 (workspace)</span>
          <span class="meta-val">${escapeHtml(mem.workspace || "未指定")}</span>
        </div>
        <div class="meta-cell">
          <span class="meta-label">会话 (conversation_id)</span>
          <span class="meta-val">${escapeHtml(mem.conversation_id || "default")}</span>
        </div>
      </div>

      <section class="insp-section">
        <h3 class="insp-sec-title">版本演进与 Diff (${mem.version_count} 个版本)</h3>
        <div class="revision-list">${revisionsHtml}</div>
      </section>

      <section class="insp-section">
        <h3 class="insp-sec-title">原始对话证据 (${(mem.sources || []).length} 条来源)</h3>
        <div class="revision-list">${sourcesHtml}</div>
      </section>

      <section class="insp-section">
        <h3 class="insp-sec-title">1-Hop 证据与实体关联图</h3>
        <div class="ego-svg-wrap">${buildEgoSvg(mem)}</div>
        <div class="neighbor-list">${neighborsHtml}</div>
      </section>
    `;

    contentEl.querySelectorAll(".neighbor-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-jump");
        state.kindFilter = "all";
        state.entityFilter = "";
        state.selectedLogicalId = target;
        syncNavButtons();
        renderEntities(state.raw.facets.entities);
        renderStream();
      });
    });
  }

  function syncNavButtons() {
    document.querySelectorAll("#kind-filters .nav-item").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-kind") === state.kindFilter);
    });
  }

  // Event Bindings
  document.querySelectorAll("#kind-filters .nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.kindFilter = btn.getAttribute("data-kind");
      syncNavButtons();
      renderStream();
    });
  });

  el("select-workspace").addEventListener("change", (e) => {
    state.workspaceFilter = e.target.value;
    el("reset-scope").hidden = !(state.workspaceFilter || state.conversationFilter);
    renderStream();
  });

  el("select-conversation").addEventListener("change", (e) => {
    state.conversationFilter = e.target.value;
    el("reset-scope").hidden = !(state.workspaceFilter || state.conversationFilter);
    renderStream();
  });

  el("reset-scope").addEventListener("click", () => {
    state.workspaceFilter = "";
    state.conversationFilter = "";
    populateScopeSelects(state.raw.facets);
    renderStream();
  });

  el("reset-entity").addEventListener("click", () => {
    state.entityFilter = "";
    renderEntities(state.raw.facets.entities);
    renderStream();
  });

  el("search-input").addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderStream();
  });

  el("clear-search").addEventListener("click", () => {
    el("search-input").value = "";
    state.searchQuery = "";
    renderStream();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== el("search-input")) {
      e.preventDefault();
      el("search-input").focus();
    } else if (e.key === "Escape" && document.activeElement === el("search-input")) {
      el("search-input").value = "";
      state.searchQuery = "";
      el("search-input").blur();
      renderStream();
    }
  });

  el("tm-slider").addEventListener("input", (e) => {
    const idx = Number(e.target.value);
    state.selectedCheckpointIdx = idx;
    const cutoff = state.checkpoints[idx];
    const isLatest = idx >= state.checkpoints.length - 1;
    loadLedger(isLatest ? null : cutoff);
  });

  el("tm-reset").addEventListener("click", () => {
    if (!state.checkpoints.length) return;
    state.selectedCheckpointIdx = state.checkpoints.length - 1;
    el("tm-slider").value = String(state.selectedCheckpointIdx);
    loadLedger(null);
  });

  el("btn-demo-toggle").addEventListener("click", () => {
    state.useDemo = !state.useDemo;
    state.selectedCheckpointIdx = null;
    el("btn-demo-toggle").classList.toggle("active-demo", state.useDemo);
    loadLedger(null);
  });

  el("btn-refresh").addEventListener("click", () => {
    state.selectedCheckpointIdx = null;
    loadLedger(null);
  });

  loadLedger(null);
})();
