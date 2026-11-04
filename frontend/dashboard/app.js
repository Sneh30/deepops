(() => {
  "use strict";

  const TILES = [
    ["COMP-09", "Provisioning", "#39d0ff"],
    ["COMP-08", "Deploy", "#5cf2a0"],
    ["COMP-10", "Proxy/TLS", "#ffb547"],
    ["COMP-06", "Metrics", "#b58cff"],
    ["COMP-07", "Incident Engine", "#ff5c8a"],
    ["COMP-11", "Secrets", "#4ce0d2"],
    ["COMP-12", "Audit", "#f2e85c"],
    ["COMP-04", "Approval Gate", "#ff8a5c"],
  ];
  const TILE_IDS = new Set(TILES.map((item) => item[0]));
  const TILE_FOR_TOOL = {
    deploy_windows_service: "COMP-08",
    deploy_windows_container: "COMP-08",
    configure_reverse_proxy: "COMP-10",
  };
  const TRACE_KINDS = {
    "agent.thinking": "THINKING",
    "agent.decision": "DECISION",
    "tool.started": "TOOL_CALL",
    "tool.previewed": "TOOL_CALL",
    "approval.requested": "GATE",
    "approval.granted": "GATE",
    "approval.rejected": "GATE",
    "approval.consumed": "GATE",
    "checkpoint.created": "CHECKPOINT",
    "checkpoint.restored": "CHECKPOINT",
    "tool.completed": "RESULT",
    "tool.failed": "RESULT",
    "tool.blocked": "RESULT",
    "agent.result": "RESULT",
    "incident.opened": "INCIDENT",
    "incident.remediated": "INCIDENT",
    "incident.resolved": "INCIDENT",
    "incident.escalated": "INCIDENT",
    "deployment.phase": "DEPLOY",
    "deployment.completed": "DEPLOY",
    "deployment.failed": "DEPLOY",
    "deployment.rolled_back": "DEPLOY",
    "provision.planned": "PROVISION",
    "provision.applied": "PROVISION",
    "provision.failed": "PROVISION",
    "report.generated": "REPORT",
  };

  const $ = (id) => document.getElementById(id);

  let state = null;
  let metricsData = null;
  let traceEntries = [];
  let tokenText = "";
  let logEvents = [];
  let tickerEvents = [];
  let currentTab = "summary";
  let mode = "trace";
  let logFilter = "";
  let userSort = { key: "connected_since", direction: 1 };
  let frozen = false;
  let frozenState = null;
  let frozenAt = null;
  let knownSessions = new Set();
  let initialLoaded = false;
  let highlightUntil = new Map();
  let seenEvents = new Set();
  let eventSource = null;
  let sseErrors = 0;
  let pollTimer = null;
  let degraded = false;
  let linkState = { state: "connecting", text: "CONNECTING" };
  let metricsTimer = null;

  async function fetchJSON(path) {
    const response = await fetch(path, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`${path} -> HTTP ${response.status}`);
    }
    return response.json();
  }

  function setLink(nextState, text) {
    linkState = { state: nextState, text };
    const element = $("link-state");
    element.dataset.state = nextState;
    element.textContent = text;
  }

  function toast(message, timeout = 8000) {
    const element = $("toast");
    element.textContent = message;
    element.classList.remove("hidden");
    window.clearTimeout(element._timer);
    element._timer = window.setTimeout(() => element.classList.add("hidden"), timeout);
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString([], { hour12: false });
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], { hour12: false });
  }

  function formatUptime(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
    const total = Math.floor(seconds);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return "—";
      return Math.abs(value) >= 1000 ? value.toFixed(1) : value.toFixed(2);
    }
    return String(value);
  }

  function sparkline(values, width = 52, height = 14, color = "#39d0ff") {
    const numbers = (Array.isArray(values) ? values : []).map((value) => Number(value) || 0);
    const maximum = Math.max(1, ...numbers);
    const step = numbers.length > 1 ? width / (numbers.length - 1) : width;
    const points = numbers
      .map((value, index) => {
        const x = index * step;
        const y = height - (value / maximum) * (height - 2) - 1;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><polyline fill="none" stroke="${color}" stroke-width="1.4" points="${points}"></polyline></svg>`;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // ------------------------------------------------------------------
  // Layout: tiles
  // ------------------------------------------------------------------
  function ensureTiles() {
    const container = $("tiles");
    if (container.children.length) return;
    for (const [component, name, color] of TILES) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tile";
      button.dataset.component = component;
      button.dataset.status = "unknown";
      button.style.setProperty("--tile-accent", color);
      button.setAttribute("role", "listitem");
      button.title = "Open component detail in Observability";
      const nameNode = element("span", "tile-name", name);
      const stateNode = element("span", "tile-state");
      const dot = element("span", "dot");
      stateNode.append(dot, element("span", "", "NOT CONNECTED"));
      button.append(nameNode, stateNode);
      button.addEventListener("click", () => {
        logFilter = component;
        const select = $("log-filter");
        if (select) select.value = component;
        openTab("observability");
        renderObservability();
      });
      container.append(button);
    }
  }

  function renderTiles() {
    ensureTiles();
    if (!state) return;
    const byId = new Map((state.tiles || []).map((tile) => [tile.component, tile]));
    for (const button of document.querySelectorAll(".tile")) {
      const tile = byId.get(button.dataset.component);
      if (!tile) continue;
      button.dataset.status = tile.status || "unknown";
      const stateNode = button.querySelector(".tile-state");
      const label = tile.status === "unknown"
        ? "NOT CONNECTED"
        : `${String(tile.status).toUpperCase()} · ${tile.last_event_type || "NO EVENTS"}`;
      stateNode.lastChild.textContent = label;
      button.title = `${tile.name} · ${tile.status} · ${tile.message || ""}`;
    }
  }

  function pulseTile(component) {
    const button = document.querySelector(`.tile[data-component="${component}"]`);
    if (!button) return;
    button.classList.remove("pulse");
    void button.offsetWidth;
    button.classList.add("pulse");
    window.setTimeout(() => button.classList.remove("pulse"), 700);
  }

  function tilesForEvent(event) {
    const targets = new Set();
    if (TILE_IDS.has(event.component)) targets.add(event.component);
    const explicit = event.data && event.data.tile;
    if (TILE_IDS.has(explicit)) targets.add(explicit);
    if (String(event.type).startsWith("tool.") && event.data) {
      const mapped = TILE_FOR_TOOL[event.data.tool];
      if (mapped) targets.add(mapped);
    }
    return [...targets];
  }

  // ------------------------------------------------------------------
  // Layout: access matrix
  // ------------------------------------------------------------------
  function renderMatrix() {
    const matrix = $("matrix");
    const empty = $("matrix-empty");
    if (!state) return;
    // Rebuild on every state application. Without this, each SSE state frame
    // appends a second copy of the grid below the first one.
    matrix.innerHTML = "";
    const users = state.users || [];
    const services = state.services || [];
    const adapter = state.query_adapter || {};
    const badge = $("adapter-badge");
    badge.textContent = adapter.connected
      ? `DB ADAPTER: ${adapter.adapter || "CONNECTED"}`
      : "DB ADAPTER: NOT CONNECTED";
    badge.className = `badge ${adapter.connected ? "ok" : "muted"}`;
    badge.title = adapter.reason || "";

    if (!users.length || !services.length) {
      matrix.innerHTML = "";
      matrix.style.display = "none";
      empty.classList.remove("hidden");
      $("matrix-empty-detail").textContent = adapter.connected
        ? "Adapter connected; waiting for the first session.opened event."
        : (adapter.reason || "A named per-database adapter is the remaining deployment-specific wiring (ADR-008).");
      return;
    }
    empty.classList.add("hidden");
    matrix.style.display = "grid";
    matrix.style.gridTemplateColumns = `220px repeat(${services.length}, minmax(78px, 1fr))`;

    const corner = element("div", "corner", "USER / SERVICE");
    matrix.append(corner);
    const colors = new Map();
    for (const service of services) {
      colors.set(service.name, service.color);
      const head = element("div", "col-head");
      head.style.setProperty("--accent", service.color);
      head.title = `${service.name} · ${service.query_count} queries · ${service.denied_count} denied`;
      head.append(
        element("span", "svc-name", service.name),
        element("span", "svc-rate", `${service.request_rate.toFixed(2)}/s`)
      );
      const chart = document.createElement("div");
      chart.innerHTML = sparkline(service.sparkline, 52, 14, service.color);
      if (chart.firstChild) head.append(chart.firstChild);
      matrix.append(head);
    }

    const cellMap = new Map(
      (state.cells || []).map((cell) => [`${cell.session_id}\u0000${cell.service}`, cell])
    );

    for (const user of users) {
      const head = element("div", "row-head");
      if (user.status === "closing") head.classList.add("closing");
      const until = highlightUntil.get(user.session_id) || 0;
      if (Date.now() < until) head.classList.add("new-row");
      head.title = `${user.user} @ ${user.ip} · since ${formatDateTime(user.connected_since)}`;
      const who = element("div", "who");
      who.append(
        element("strong", "", user.user),
        element("span", "", user.ip || "no IP")
      );
      head.append(who, element("span", "count", `${user.service_count} SVC`));
      matrix.append(head);

      for (const service of services) {
        const cell = cellMap.get(`${user.session_id}\u0000${service.name}`);
        const stateName = cell ? cell.state : "no_access";
        const node = element("div", `cell ${stateName.replace("_", "-")}`);
        node.style.setProperty("--accent", service.color);
        node.dataset.session = user.session_id;
        node.dataset.service = service.name;
        node.dataset.state = stateName;
        const access = cell && cell.access ? ` · ${cell.access}` : "";
        const last = cell && cell.last_query_at ? ` · last query ${formatTime(cell.last_query_at)}` : "";
        node.title = `${user.user} × ${service.name}: ${stateName.replace("_", " ")}${access}${last}`;
        node.addEventListener("click", () => showCellDetail(user, service, cell));
        matrix.append(node);
      }
    }
  }

  function showCellDetail(user, service, cell) {
    const detail = $("cell-detail");
    detail.innerHTML = "";
    const stateName = cell ? cell.state : "no_access";
    detail.append(
      element("strong", "", `${user.user} × ${service.name}`),
      document.createTextNode(
        ` · ${stateName.replace("_", " ")}` +
        (cell && cell.access ? ` · access=${cell.access}` : "") +
        ` · last query=${cell && cell.last_query_at ? formatDateTime(cell.last_query_at) : "none observed"}`
      )
    );
    detail.classList.remove("hidden");
    if (currentTab === "observability") {
      logFilter = service.name;
      renderObservability();
    }
  }

  // ------------------------------------------------------------------
  // Layout: users table
  // ------------------------------------------------------------------
  function renderUsers() {
    if (!state) return;
    const body = $("users-body");
    body.innerHTML = "";
    const users = [...(state.users || [])];
    const direction = userSort.direction;
    users.sort((left, right) => {
      const a = left[userSort.key];
      const b = right[userSort.key];
      if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
      return String(a || "").localeCompare(String(b || "")) * direction;
    });
    for (const user of users) {
      const row = document.createElement("tr");
      if (user.status === "closing") row.classList.add("closing");
      const until = highlightUntil.get(user.session_id) || 0;
      if (Date.now() < until) row.classList.add("new-row");
      row.append(
        element("td", "", user.user),
        element("td", "", user.ip || "—"),
        element("td", "", formatDateTime(user.connected_since)),
        element("td", "", user.service_count),
        element("td", "", user.status.toUpperCase())
      );
      body.append(row);
    }
    const open = users.filter((user) => user.status === "open").length;
    $("user-count").textContent = `${open} OPEN / ${users.length} VISIBLE`;
  }

  // ------------------------------------------------------------------
  // Layout: summary
  // ------------------------------------------------------------------
  function gauge(label, value, sub, className = "") {
    const node = element("div", "gauge");
    node.append(element("div", "label", label));
    node.append(element("div", `value ${className}`, value));
    if (sub) node.append(element("div", "sub", sub));
    return node;
  }

  function renderKv(containerId, values, emptyText) {
    const container = $(containerId);
    container.innerHTML = "";
    const entries = Object.entries(values || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
    if (!entries.length) {
      container.append(element("div", "kv", emptyText));
      return;
    }
    for (const [key, value] of entries) {
      const row = element("div", "kv");
      row.append(element("span", "", key), element("span", "", String(value)));
      container.append(row);
    }
  }

  function renderSummary() {
    if (!state) return;
    const summary = state.summary || {};
    const control = summary.control || {};
    const instrument = $("instrument");
    instrument.innerHTML = "";
    instrument.append(
      gauge("CONTROL PLANE UPTIME", formatUptime(control.uptime_seconds), control.heartbeat_fresh ? "heartbeat fresh" : "heartbeat stale", "small"),
      gauge("CONNECTED USERS", summary.connected_users ?? 0, `${summary.total_users ?? 0} visible row(s)`),
      gauge("ACTIVE INCIDENTS", summary.active_incidents ?? 0, (summary.active_incidents || 0) > 0 ? "investigate now" : "none active"),
      gauge("SERVICES", summary.total_services ?? 0, "real columns observed"),
      gauge("PENDING APPROVALS", summary.pending_approvals ?? 0, "human decisions waiting"),
      gauge("EVENTS SEEN", summary.events_seen ?? 0, "journal events applied", "small"),
      gauge("QUERY ADAPTER", (state.query_adapter || {}).connected ? "LIVE" : "OFFLINE", (state.query_adapter || {}).adapter || "ADR-008 extension point", "small"),
      gauge("ACTIVE QUERIES", summary.active_queries ?? 0, `${summary.stale_queries ?? 0} stale timeout(s)`)
    );

    const deployment = summary.last_deployment;
    renderKv(
      "last-deployment",
      deployment
        ? {
            app: deployment.app || "—",
            version: deployment.version || "—",
            slot: deployment.slot || "—",
            phase: deployment.phase || "—",
            status: deployment.status || "—",
            at: formatDateTime(deployment.at),
          }
        : {},
      "No deployment event observed."
    );

    const approvals = summary.approvals || [];
    const approvalValues = {};
    approvals.forEach((item, index) => {
      approvalValues[`${index + 1}. ${item.tool_name || "tool"}`] = `${item.request_id || "?"} · ${(item.risk_categories || []).join(",") || "risk"}`;
    });
    renderKv("pending-approvals", approvalValues, "No pending approvals.");

    const report = summary.weekly_report;
    renderKv(
      "weekly-report",
      report
        ? {
            path: report.path || "—",
            period: `${formatDateTime(report.period_start)} → ${formatDateTime(report.period_end)}`,
            incidents: report.incidents ? JSON.stringify(report.incidents) : "—",
            generated: formatDateTime(report.at),
          }
        : {},
      "No weekly report generated yet."
    );

    const utilization = summary.latest_utilization || {};
    const utilizationValues = {};
    for (const [app, sample] of Object.entries(utilization)) {
      utilizationValues[app] = `CPU ${formatValue(sample.cpu_percent)}% · MEM ${formatValue(sample.memory_mb)} MB · DISK ${formatValue(sample.disk_percent)}%`;
    }
    renderKv("utilization", utilizationValues, "No utilization sample yet.");
  }

  // ------------------------------------------------------------------
  // Layout: observability
  // ------------------------------------------------------------------
  function renderObservability() {
    if (!state) return;
    const status = $("metrics-status");
    const metrics = metricsData || {};
    if (metrics.connected) {
      status.textContent = "PROMETHEUS: LIVE";
      status.className = "badge ok";
    } else if (metrics.last_error) {
      status.textContent = "PROMETHEUS: ERROR";
      status.className = "badge warn";
    } else {
      status.textContent = "PROMETHEUS: WAITING";
      status.className = "badge muted";
    }
    status.title = metrics.last_error || "";

    const cards = $("metric-cards");
    cards.innerHTML = "";
    const series = metrics.series || [];
    if (!series.length) {
      const note = element("div", "metric-empty");
      note.textContent = metrics.last_error
        ? `No Prometheus samples: ${metrics.last_error}`
        : "Waiting for the first real Prometheus scrape (COMP-06).";
      cards.append(note);
    } else {
      const grouped = new Map();
      for (const item of series) {
        if (!grouped.has(item.name)) grouped.set(item.name, []);
        grouped.get(item.name).push(item);
      }
      for (const [name, items] of grouped) {
        const first = items[0];
        const latest = first.points.length ? first.points[first.points.length - 1][1] : null;
        const card = element("div", "metric-card");
        card.append(element("div", "name", labelForMetric(name)));
        card.append(element("div", "value", formatValue(latest)));
        const chart = document.createElement("div");
        chart.innerHTML = sparkline(first.points.map((point) => point[1]), 120, 26, "#5cf2a0");
        if (chart.firstChild) card.append(chart.firstChild);
        card.title = `${name} · ${Object.entries(first.labels || {}).map(([key, value]) => `${key}=${value}`).join(" ")}`;
        cards.append(card);
      }
    }

    renderLogSelect();
    const stream = $("log-stream");
    const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 60;
    stream.innerHTML = "";
    const filtered = logEvents.filter((event) => {
      if (!logFilter) return true;
      return event.component === logFilter || (event.context && (event.context.service === logFilter || event.context.tool === logFilter));
    });
    if (!filtered.length) {
      stream.append(element("div", "thinking-note", "No structured log lines matched this filter."));
    }
    for (const event of filtered) {
      const line = element("div", "log-line");
      line.dataset.level = event.level || "INFO";
      line.append(
        element("span", "ts", formatTime(event.timestamp)),
        element("span", "lvl", event.level || "INFO"),
        element("span", "comp", event.component || "—"),
        element("span", "msg", event.message || "")
      );
      stream.append(line);
    }
    if (nearBottom) stream.scrollTop = stream.scrollHeight;
  }

  function labelForMetric(name) {
    return String(name).replace(/_/g, " ").toUpperCase();
  }

  function renderLogSelect() {
    const select = $("log-filter");
    if (!select) return;
    const components = new Set([
      ...TILES.map((item) => item[0]),
      "COMP-01", "COMP-03", "COMP-05", "COMP-13",
    ]);
    for (const event of logEvents) if (event.component) components.add(event.component);
    const current = logFilter;
    select.innerHTML = "";
    select.append(new Option("ALL COMPONENTS", ""));
    for (const component of [...components].sort()) {
      select.append(new Option(component, component));
    }
    select.value = current;
  }

  // ------------------------------------------------------------------
  // Layout: agent thinking
  // ------------------------------------------------------------------
  function renderThinking() {
    const traceStream = $("trace-stream");
    const tokenStream = $("token-stream");
    const note = $("thinking-note");
    const status = $("thinking-status");
    const useTokens = mode === "tokens" && tokenText.length > 0;
    traceStream.classList.toggle("hidden", useTokens);
    tokenStream.classList.toggle("hidden", !useTokens);
    note.classList.toggle("hidden", !(mode === "tokens" && !tokenText.length));
    if (mode === "tokens" && !tokenText.length) {
      note.textContent = "No raw token stream for the current step — showing the structured trace instead (UI_SPEC.md §5.3 Mode B fallback).";
    }
    status.textContent = mode === "tokens"
      ? `TOKENS: ${tokenText.length} CHARS`
      : `TRACE: ${traceEntries.length} ENTRIES`;

    if (useTokens) {
      tokenStream.textContent = tokenText;
      tokenStream.scrollTop = tokenStream.scrollHeight;
      return;
    }
    const nearBottom = traceStream.scrollHeight - traceStream.scrollTop - traceStream.clientHeight < 60;
    traceStream.innerHTML = "";
    const entries = traceEntries.slice(-300);
    if (!entries.length) {
      traceStream.append(element("div", "thinking-note", "No agent decision events observed yet."));
    }
    for (const entry of entries) {
      const line = element("div", "trace-line");
      line.dataset.kind = entry.kind || "TRACE";
      line.append(
        element("span", "ts", formatTime(entry.timestamp)),
        element("span", "kind", entry.kind || "TRACE"),
        element("span", "msg", entry.message || "")
      );
      traceStream.append(line);
    }
    if (nearBottom) traceStream.scrollTop = traceStream.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Layout: ticker
  // ------------------------------------------------------------------
  function renderTicker() {
    const ticker = $("ticker");
    ticker.innerHTML = "";
    const events = tickerEvents.slice(-80).reverse();
    for (const event of events) {
      const line = element("div", "ticker-line");
      line.dataset.level = event.level || "INFO";
      line.append(
        element("span", "ts", formatTime(event.timestamp)),
        element("span", "comp", event.component || "—"),
        element("span", "msg", event.message || "")
      );
      ticker.append(line);
    }
    const cutoff = Date.now() - 60000;
    const perMinute = tickerEvents.filter((event) => {
      const when = new Date(event.timestamp).getTime();
      return Number.isFinite(when) && when >= cutoff;
    }).length;
    $("ticker-rate").textContent = `${perMinute}/MIN`;
  }

  // ------------------------------------------------------------------
  // State application and live events
  // ------------------------------------------------------------------
  function applyState(nextState) {
    if (!nextState) return;
    if (frozen) {
      frozenState = nextState;
      return;
    }
    const previous = knownSessions;
    knownSessions = new Set((nextState.users || []).map((user) => user.session_id));
    if (initialLoaded) {
      const until = Date.now() + 2000;
      for (const sessionId of knownSessions) {
        if (!previous.has(sessionId)) highlightUntil.set(sessionId, until);
      }
    }
    state = nextState;
    renderAll();
  }

  function renderAll() {
    renderTiles();
    renderMatrix();
    renderUsers();
    renderSummary();
    renderObservability();
    renderThinking();
    renderTicker();
  }

  function handleEvent(event) {
    if (!event || !event.id || seenEvents.has(event.id)) return;
    seenEvents.add(event.id);
    if (seenEvents.size > 2000) {
      const first = seenEvents.values().next().value;
      seenEvents.delete(first);
    }
    if (frozen) return;
    for (const component of tilesForEvent(event)) pulseTile(component);
    tickerEvents.push(event);
    if (tickerEvents.length > 500) tickerEvents = tickerEvents.slice(-300);
    const traceKind = TRACE_KINDS[event.type];
    if (traceKind) {
      traceEntries.push({
        timestamp: event.timestamp,
        kind: traceKind,
        message: event.message,
        component: event.component,
        data: event.data || {},
      });
      if (traceEntries.length > 600) traceEntries = traceEntries.slice(-400);
    }
    if (event.type === "agent.token" && event.data) {
      const token = event.data.token || event.data.text || event.data.delta || "";
      if (token) {
        tokenText += String(token);
        if (tokenText.length > 50000) tokenText = tokenText.slice(-50000);
      }
    }
    renderTicker();
    if (traceKind || event.type === "agent.token") renderThinking();
  }

  function seedFromState(nextState) {
    if (!nextState) return;
    const recent = nextState.recent_events || [];
    tickerEvents = recent.slice(-80);
    for (const event of recent) {
      if (event.id) seenEvents.add(event.id);
    }
  }

  // ------------------------------------------------------------------
  // Tabs and controls
  // ------------------------------------------------------------------
  function openTab(tab) {
    currentTab = tab;
    for (const button of document.querySelectorAll(".tab-button")) {
      const active = button.dataset.tab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
    for (const panel of document.querySelectorAll(".tab-panel")) {
      panel.classList.toggle("active", panel.id === `tab-${tab}`);
    }
  }

  function toggleFreeze() {
    frozen = !frozen;
    const button = $("freeze-toggle");
    const banner = $("frozen-banner");
    button.setAttribute("aria-pressed", frozen ? "true" : "false");
    button.textContent = frozen ? "RESUME LIVE" : "FREEZE FRAME";
    if (frozen) {
      frozenAt = new Date();
      banner.textContent = `FREEZE FRAME ACTIVE · captured ${formatTime(frozenAt)} · live updates paused (not the backend)`;
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
      if (frozenState) {
        applyState(frozenState);
        frozenState = null;
      } else {
        refreshState();
      }
    }
  }

  function selectFilter(value) {
    logFilter = value;
    const select = $("log-filter");
    if (select) select.value = value;
    renderObservability();
  }

  function bindControls() {
    for (const button of document.querySelectorAll(".tab-button")) {
      button.addEventListener("click", () => openTab(button.dataset.tab));
    }
    $("freeze-toggle").addEventListener("click", toggleFreeze);
    $("log-filter").addEventListener("change", (event) => selectFilter(event.target.value));
    for (const button of document.querySelectorAll("[data-mode]")) {
      button.addEventListener("click", () => {
        mode = button.dataset.mode;
        for (const peer of document.querySelectorAll("[data-mode]")) {
          peer.classList.toggle("active", peer === button);
        }
        renderThinking();
      });
    }
    for (const header of document.querySelectorAll("[data-sort]")) {
      header.addEventListener("click", () => {
        const key = header.dataset.sort;
        if (userSort.key === key) userSort.direction *= -1;
        else userSort = { key, direction: 1 };
        renderUsers();
      });
    }
  }

  // ------------------------------------------------------------------
  // Data refresh
  // ------------------------------------------------------------------
  async function refreshState() {
    try {
      const nextState = await fetchJSON("api/state");
      applyState(nextState);
    } catch (error) {
      setLink("down", "DASHBOARD UNREACHABLE");
    }
  }

  async function refreshSlow() {
    if (frozen) return;
    const skills = [
      fetchJSON("api/metrics").then((value) => { metricsData = value; }).catch(() => {}),
      fetchJSON("api/logs?limit=300").then((value) => { logEvents = value.events || []; }).catch(() => {}),
    ];
    await Promise.allSettled(skills);
    if (currentTab === "thinking") {
      try {
        const [trace, tokens] = await Promise.all([
          fetchJSON("api/trace?limit=300"),
          fetchJSON("api/tokens"),
        ]);
        traceEntries = trace.entries || traceEntries;
        tokenText = Object.values(tokens.text || {}).join("");
      } catch (error) {
        // Keep the last real entries; no synthetic trace is ever injected.
      }
    }
    if (degraded) await refreshState();
    renderAll();
  }

  function startPolling() {
    if (pollTimer) return;
    degraded = true;
    setLink("degraded", "DEGRADED · POLLING 2S");
    toast("SSE unavailable; switched to documented degraded polling mode (2s). Live event pulses may lag.", 10000);
    pollTimer = window.setInterval(async () => {
      try {
        const nextState = await fetchJSON("api/state");
        applyState(nextState);
        const recent = await fetchJSON("api/events/recent?limit=50");
        for (const event of recent.events || []) handleEvent(event);
      } catch (error) {
        setLink("down", "DASHBOARD UNREACHABLE");
      }
    }, 2000);
  }

  function stopPolling() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = null;
    degraded = false;
  }

  function connectSSE() {
    if (!("EventSource" in window)) {
      startPolling();
      return;
    }
    if (eventSource) eventSource.close();
    setLink("connecting", "CONNECTING");
    eventSource = new EventSource("api/events/stream");
    eventSource.onopen = () => {
      sseErrors = 0;
      stopPolling();
      setLink("live", "LIVE");
    };
    eventSource.onerror = () => {
      sseErrors += 1;
      setLink("degraded", "RECONNECTING");
      if (sseErrors >= 3) startPolling();
    };
    eventSource.addEventListener("state", (message) => {
      try { applyState(JSON.parse(message.data)); } catch (error) { /* ignore malformed frame */ }
    });
    eventSource.addEventListener("update", (message) => {
      try {
        const payload = JSON.parse(message.data);
        applyState(payload.state);
        handleEvent(payload.event);
      } catch (error) { /* ignore malformed frame */ }
    });
    eventSource.addEventListener("pulse", (message) => {
      try { handleEvent(JSON.parse(message.data).event); } catch (error) { /* ignore malformed frame */ }
    });
  }

  async function boot() {
    ensureTiles();
    bindControls();
    try {
      state = await fetchJSON("api/state");
      seedFromState(state);
      const results = await Promise.allSettled([
        fetchJSON("api/metrics"),
        fetchJSON("api/logs?limit=300"),
        fetchJSON("api/trace?limit=300"),
        fetchJSON("api/tokens"),
      ]);
      if (results[0].status === "fulfilled") metricsData = results[0].value;
      if (results[1].status === "fulfilled") logEvents = results[1].value.events || [];
      if (results[2].status === "fulfilled") traceEntries = results[2].value.entries || [];
      if (results[3].status === "fulfilled") tokenText = Object.values(results[3].value.text || {}).join("");
      knownSessions = new Set((state.users || []).map((user) => user.session_id));
      initialLoaded = true;
      renderAll();
      connectSSE();
      metricsTimer = window.setInterval(refreshSlow, 5000);
    } catch (error) {
      setLink("down", "DASHBOARD UNREACHABLE");
      toast(`Initial dashboard load failed: ${error.message}`, 12000);
    }
  }

  window.addEventListener("beforeunload", () => {
    if (eventSource) eventSource.close();
    if (pollTimer) window.clearInterval(pollTimer);
    if (metricsTimer) window.clearInterval(metricsTimer);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
