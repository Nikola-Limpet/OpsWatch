"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value) =>
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
const states = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
  stale: "Stale",
  resolved: "Recovered",
  quiet: "Quiet",
  disabled: "Disabled",
};
const state = {
  data: null,
  view: "overview",
  refreshing: false,
  failed: false,
  dialog: null,
  queryLimit: 20,
  incidentLimit: 30,
  fixPending: new Set(),
  fixErrors: new Map(),
  selectionBusy: null,
  selectionMessage: "",
  selectionError: false,
  serviceTab: "monitored",
  servicePage: 0,
  collapsedProjects: new Set(),
  selectedService: null,
  refreshPromise: null,
  selectedIncident: null,
  incidentCache: new Map(),
  mobileDetail: false,
};
const views = {
  overview: ["Overview", "A clear view of your production stack."],
  services: [
    "Projects",
    "Control monitoring for your projects and services.",
  ],
  incidents: [
    "Incidents",
    "Understand the issue. Choose the next action.",
  ],
  agents: ["Agent work", "Follow each fix, from investigation to a draft PR."],
  queries: [
    "Database queries",
    "Measured query timings from your connected databases.",
  ],
};

function age(timestamp) {
  if (!timestamp) return "Not checked";
  const elapsed = Math.max(0, Date.now() / 1000 - timestamp);
  if (elapsed < 10) return "Just now";
  if (elapsed < 60) return `${Math.floor(elapsed)}s ago`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)}h ago`;
  return `${Math.floor(elapsed / 86400)}d ago`;
}

function timestamp(value) {
  if (!value) return "No recorded check";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: state.data?.timezone,
  }).format(new Date(value * 1000));
}

function badge(status, label) {
  const known = Object.hasOwn(states, status) ? status : "unknown";
  return `<span class="badge ${known}"><span class="dot ${known === "resolved" || known === "quiet" ? "healthy" : known}"></span>${escapeHTML(label || states[known])}</span>`;
}

function incidentState(incident) {
  return incident.status === "resolved"
    ? ["application error", "security"].includes(incident.kind)
      ? "quiet"
      : "resolved"
    : incident.severity;
}

function serviceName(id) {
  return state.data?.services.find((service) => service.id === id)?.name || id;
}

function empty(title, description) {
  return `<div class="empty"><strong>${escapeHTML(title)}</strong>${escapeHTML(description)}</div>`;
}

function serviceTable(services, compact = false, manage = false) {
  if (!services.length)
    return empty(
      "No services found",
      state.data.summary.total
        ? "Try another search or filter."
        : "Enable targets in config.local.yaml to see their recorded checks here.",
    );
  return `<div class="table-wrap"><table><thead><tr><th scope="col">Service</th><th scope="col">Provider</th><th scope="col">Status</th><th scope="col">Response</th>${compact ? "" : '<th scope="col">Last checked</th>'}${manage ? '<th scope="col">Monitoring</th>' : ""}</tr></thead><tbody>${services.map((service) => `<tr class="${service.status === "disabled" ? "service-disabled" : ""}"><td><button class="service-button" data-service="${escapeHTML(service.id)}" aria-label="View ${escapeHTML(service.name)} evidence"><span class="service-icon" aria-hidden="true"><span class="service-initials">${escapeHTML(service.name.substring(0, 2).toUpperCase())}</span>${service.favicon ? `<img src="${escapeHTML(service.favicon)}" alt="" loading="lazy" decoding="async" data-favicon>` : ""}</span><span><span class="service-name">${escapeHTML(service.name)}</span><span class="service-sub">${escapeHTML(service.environment)}</span></span></button></td><td class="provider">${escapeHTML(service.provider)}</td><td>${badge(service.status)}</td><td class="latency numeric ${service.latency_ms > service.threshold_ms ? "slow" : ""}">${service.latency_ms !== null ? `${number(service.latency_ms)} <span class="inline-unit">ms</span>` : '<span class="time-cell">No sample</span>'}</td>${compact ? "" : `<td class="time-cell" title="${escapeHTML(timestamp(service.checked))}">${service.enabled === false ? "Paused" : age(service.checked)}</td>`}${manage ? `<td>${monitoringButton("services", service.id, service.name, service.enabled)}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

function monitoringButton(scope, id, name, enabled, count) {
  const pending = state.selectionBusy === `${scope}:${id}`;
  const label = pending
    ? "Saving…"
    : scope === "projects" ? `${enabled ? "Pause" : "Enable"} all ${count}` : enabled ? "Pause" : "Enable";
  const description = scope === "projects"
    ? `${enabled ? "Pause" : "Enable"} all ${count} service${count === 1 ? "" : "s"} in ${name}`
    : `${enabled ? "Pause" : "Enable"} monitoring for ${name}`;
  return `<button class="button monitoring-button" data-monitor-scope="${scope}" data-monitor-id="${escapeHTML(id)}" data-monitor-enabled="${!enabled}" aria-label="${escapeHTML(description)}" title="${escapeHTML(description)}" ${state.selectionBusy || state.failed || !state.data.selection_editable ? "disabled" : ""} ${pending ? 'aria-busy="true"' : ""}>${label}</button>`;
}

function monitoringSwitch(service, context = "list") {
  const pending = state.selectionBusy === `services:${service.id}` ||
    (state.selectionBusy?.startsWith("projects:") && (state.data.projects || [])
      .some((p) => state.selectionBusy === `projects:${p.id}` && p.services.some((s) => s.id === service.id)));
  const enabled = service.enabled !== false;
  const unavailable = state.selectionBusy || state.failed || !state.data.selection_editable;
  return `<button class="monitoring-switch" role="switch" aria-checked="${enabled}" aria-label="Monitoring for ${escapeHTML(service.name)}" data-monitor-scope="services" data-monitor-id="${escapeHTML(service.id)}" data-monitor-context="${context}" ${context === "details" ? 'data-settings-focus="monitoring"' : ""} data-monitor-enabled="${!enabled}" ${unavailable ? "disabled" : ""} ${pending ? 'aria-busy="true"' : ""}><span class="switch-track" aria-hidden="true"><span></span></span><span class="switch-label">${pending ? "Saving…" : enabled ? "On" : "Off"}</span></button>`;
}

function latestJob(ref) {
  return state.data.repairs?.find((job) => job.incident === ref);
}

function jobPresentation(job) {
  const statuses = {
    queued: ["Queued", "unknown", 0, true],
    preparing: ["Preparing workspace", "unknown", 0, true],
    coding: ["Investigating & fixing", "unknown", 1, true],
    validating: ["Running checks", "unknown", 2, true],
    publishing: ["Preparing draft PR", "unknown", 2, true],
    pr_open: ["Draft PR ready", "healthy", 3, false],
    blocked: ["Fix blocked", "warning", -1, false],
    interrupted: ["Needs manual review", "warning", -1, false],
    cancelled: ["Cancelled", "unknown", -1, false],
    cancel_requested: ["Stopping fix", "warning", -1, true],
  };
  const [label, tone, phase, active] = statuses[job?.status] || ["Unknown status", "unknown", -1, false];
  return { label, tone, phase, active };
}

function canStartRepair(item, job) {
  const legacyQuiet = Object.keys(item.evidence || {}).length === 1 && Object.hasOwn(item.evidence, "quiet_seconds");
  return Boolean(
    item.repair?.available && !state.failed && state.data.monitor.status === "running" &&
    !legacyQuiet && Object.keys(item.evidence || {}).length > 0 &&
    (!job || ["blocked", "cancelled"].includes(job.status)),
  );
}

function workspaceHealth() {
  const { summary, monitor } = state.data;
  const fresh = !state.failed && monitor.status === "running";
  const repairs = state.data.repairs || [];
  return {
    healthy: fresh ? summary.healthy : 0,
    attention: fresh ? summary.attention : 0,
    unknown: fresh ? summary.unknown : summary.total,
    gaps: new Set(state.data.incidents.filter((i) => i.status === "open" && i.kind === "monitoring").map((i) => i.target)).size,
    worker: fresh ? state.data.repair_worker?.status || "unverified" : "unverified",
    active: repairs.filter((job) => jobPresentation(job).active).length,
    review: repairs.filter((job) => job.status === "pr_open").length,
    blocked: repairs.filter((job) => ["blocked", "interrupted"].includes(job.status)).length,
  };
}

function groupIncidents(incidents) {
  const groups = new Map();
  for (const item of incidents) {
    const service = state.data.services.find((s) => s.id === item.target);
    // Account aliases must be known before suggesting a shared provider failure.
    const key = item.kind === "monitoring" && item.status === "open" &&
      typeof item.evidence?.error === "string" && service?.account
      ? JSON.stringify([service.provider, service.account, item.evidence.error])
      : item.ref;
    if (!groups.has(key)) groups.set(key, { id: item.ref, items: [] });
    groups.get(key).items.push(item);
  }
  return [...groups.values()];
}

function filteredIncidents() {
  const search = $("incident-search").value.trim().toLowerCase();
  const status = $("incident-status").value || "open";
  return state.data.incidents.filter((item) =>
    (status === "all" || (status === "active" ? jobPresentation(latestJob(item.ref)).active : item.status === status)) &&
    `${item.ref} ${item.title} ${serviceName(item.target)} ${item.kind}`.toLowerCase().includes(search),
  );
}

function renderSummary() {
  const summary = state.data.summary;
  const health = workspaceHealth();
  const workerLabel = health.worker === "running" ? "Worker online" : health.worker === "stale" ? "Worker heartbeat stale" : health.worker === "not_started" ? "Worker not started" : "Worker unverified";
  $("summary").hidden = false;
  $("summary").innerHTML = `
    <div class="health-item"><span class="health-symbol" aria-hidden="true">${icon("pulse")}</span><div><span class="health-heading">Recorded service health <span class="health-total">${number(summary.total)} monitored</span></span><p><span><strong class="healthy">${number(health.healthy)}</strong> healthy</span><span><strong class="warning">${number(health.attention)}</strong> need attention</span><span><strong>${number(health.unknown)}</strong> unverified</span></p></div></div>
    <div class="health-item"><span class="health-symbol" aria-hidden="true">${icon("shield")}</span><div><span class="health-heading">Monitoring coverage</span><p>${state.failed || state.data.monitor.status !== "running" ? '<span class="warning">Current coverage unverified</span>' : health.gaps ? `<span><strong class="warning">${number(health.gaps)}</strong> services with collection errors</span>` : '<span><span class="dot healthy"></span> No collection errors recorded</span>'}</p></div></div>
    <a class="health-item health-agent" href="#agents"><span class="health-symbol" aria-hidden="true">${icon("agent")}</span><div><span class="health-heading">Agent work <span class="health-total">${escapeHTML(workerLabel)}</span></span><p><span><strong>${number(health.active)}</strong> active</span><span><strong class="${health.review ? "healthy" : ""}">${number(health.review)}</strong> to review</span>${health.blocked ? `<span><strong class="warning">${number(health.blocked)}</strong> blocked</span>` : ""}</p></div></a>`;
  $("nav-services").textContent = state.data.projects?.length || summary.total;
  $("nav-incidents").textContent = summary.open_incidents;
  $("nav-agents").textContent = health.active + health.review + health.blocked;
  $("worker-state").textContent = workerLabel;
  renderMonitor();
}

function icon(name) {
  const paths = {
    pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    shield: '<path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Z"/><path d="m8 12 3 3 5-6"/>',
    agent: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5"/>',
    alert: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
    arrow: '<path d="m9 5 7 7-7 7"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    settings: '<path d="m9 3-.6 2.1-1.8 1-2.1-.5L2 10l1.5 1.6v2L2 15l2.5 4.4 2.1-.5 1.8 1L9 22h5l.6-2.1 1.8-1 2.1.5L21 15l-1.5-1.4v-2L21 10l-2.5-4.4-2.1.5-1.8-1L14 3Z"/><circle cx="11.5" cy="12.5" r="3"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.pulse}</svg>`;
}

function renderMonitor() {
  const { monitor, summary } = state.data;
  const stale = monitor.status !== "running" || state.failed;
  $("monitor-state").textContent = state.failed
    ? "Dashboard disconnected"
    : monitor.status === "running"
      ? "Local monitor online"
      : monitor.status === "stale"
        ? "Monitor data is stale"
        : "Monitor not started";
  $("monitor-dot").className = `dot ${stale ? "warning" : "healthy"}`;
  $("monitor-time").textContent = monitor.last_cycle
    ? `Last cycle ${age(monitor.last_cycle)}`
    : "Waiting for the first cycle";
  $("refresh-label").textContent =
    `${state.failed ? "Last received" : "Updated"} ${age(state.data.generated_at)}`;
  $("timezone-label").textContent = state.data.timezone.replaceAll("_", " ");
  const banner = $("health-banner");
  banner.className = `notice ${stale || summary.attention || summary.unknown ? "warning" : ""}`;
  let message;
  if (state.failed)
    message =
      "<strong>Connection lost.</strong> Showing the last received snapshot. Current service health is unverified.";
  else if (monitor.status === "not_started")
    message =
      "<strong>Waiting for monitor data.</strong> Run opswatch run to start collecting checks.";
  else if (monitor.status === "stale")
    message =
      "<strong>Monitor readings are stale.</strong> Check the local monitor before relying on these results.";
  else if (workspaceHealth().gaps)
    message = `<strong>Some production signals are unavailable.</strong> Collection failures need attention; application health may be unverified.`;
  else if (summary.attention)
    message = `<strong>${number(summary.attention)} service${summary.attention === 1 ? " needs" : "s need"} attention.</strong> Review the recorded evidence before taking action.`;
  else if (summary.unknown)
    message = `<strong>${number(summary.unknown)} service${summary.unknown === 1 ? " has" : "s have"} unverified health.</strong> Review missing checks and monitoring coverage.`;
  else if (!summary.total)
    message =
      "<strong>Your workspace is ready.</strong> Add enabled targets to your configuration to begin monitoring.";
  else
    message = `<strong>All ${number(summary.total)} monitored services are healthy.</strong> Current recorded checks are passing.`;
  banner.innerHTML = `<span class="notice-icon" aria-hidden="true">${stale || summary.attention || summary.unknown ? "ⓘ" : "✓"}</span><span>${message}</span><a class="text-link" href="#${summary.attention || summary.open_incidents ? "incidents" : "services"}">Review <span aria-hidden="true">→</span></a>`;
  banner.hidden = !stale && summary.total > 0 && (state.view === "incidents" || (!summary.attention && !summary.unknown));
}

function renderOverview() {
  const data = state.data;
  $("overview-services-count").textContent = data.summary.total;
  $("overview-services").innerHTML = serviceTable(
    data.services.slice(0, 6),
    true,
  );
  $("recent-incidents").innerHTML =
    data.incidents
      .slice(0, 4)
      .map(
        (incident) =>
          `<button class="incident-card" data-incident="${escapeHTML(incident.ref)}" aria-label="View incident ${escapeHTML(incident.ref)}"><div class="incident-top">${badge(incidentState(incident))}<time title="${escapeHTML(timestamp(incident.updated))}">${age(incident.updated)}</time></div><h3>${escapeHTML(serviceName(incident.target))}</h3><p>${escapeHTML(incident.title)}</p><div class="incident-bottom"><span class="mono">${escapeHTML(incident.ref)}</span><span>View evidence ↗</span></div></button>`,
      )
      .join("") ||
    empty(
      "No incidents recorded",
      "Incidents will appear here as the monitor records them.",
    );
  const samples = data.services
    .filter((s) => s.latency_ms !== null)
    .sort((a, b) => b.latency_ms - a.latency_ms)
    .slice(0, 6);
  const scale = Math.max(1, ...samples.map((s) => s.latency_ms));
  $("response-times").innerHTML = samples.length
    ? `<div class="response-grid">${samples.map((s) => `<div><div class="response-label"><span>${escapeHTML(s.name)}</span><strong>${number(s.latency_ms)} ms</strong></div><progress class="response-track ${s.latency_ms > s.threshold_ms ? "slow" : ""}" aria-label="${escapeHTML(s.name)} response in milliseconds" value="${s.latency_ms}" max="${scale}" aria-valuetext="${number(s.latency_ms)} milliseconds; threshold ${number(s.threshold_ms)} milliseconds">${number(s.latency_ms)} ms</progress></div>`).join("")}</div>`
    : empty(
        "No current HTTP samples",
        "Response times appear after a reachable HTTP endpoint is checked.",
      );
}

function filteredServiceProjects() {
  const search = $("service-search").value.trim().toLowerCase();
  const provider = $("provider-filter").value;
  const status = state.serviceTab === "inactive" ? "" : $("status-filter").value;
  return (state.data.projects || []).map((project) => ({
    ...project,
    visible: project.services.filter((s) =>
      (state.serviceTab === "inactive" ? s.enabled === false : s.enabled !== false) &&
      `${project.name} ${s.name} ${s.id} ${s.provider}`.toLowerCase().includes(search) &&
      (!provider || s.provider === provider) &&
      (!status || (status === "attention" ? ["warning", "critical"].includes(s.status) :
        status === "unknown" ? ["unknown", "stale"].includes(s.status) : s.status === status)),
    ),
  })).filter((p) => p.visible.length).sort((a, b) => {
    const rank = (p) => p.visible.some((s) => ["critical", "warning"].includes(s.status)) ? 0 : 1;
    return rank(a) - rank(b) || a.name.localeCompare(b.name);
  });
}

function serviceRow(service) {
  const selected = state.selectedService === service.id;
  return `<tr class="managed-service ${service.enabled === false ? "service-disabled" : ""} ${selected ? "is-selected" : ""}">
    <td><button class="service-button" data-service-settings="${escapeHTML(service.id)}" aria-label="Configure ${escapeHTML(service.name)}" aria-expanded="${selected}" aria-controls="service-settings"><span class="service-icon" aria-hidden="true"><span class="service-initials">${escapeHTML(service.name.substring(0, 2).toUpperCase())}</span>${service.favicon ? `<img src="${escapeHTML(service.favicon)}" alt="" loading="lazy" decoding="async" data-favicon>` : ""}</span><span><span class="service-name">${escapeHTML(service.name)}</span><span class="service-sub">${escapeHTML(service.environment)}</span></span></button></td>
    <td class="provider">${escapeHTML(service.provider)}</td>
    <td>${badge(service.status, service.enabled === false ? "Paused" : undefined)}</td>
    <td class="latency numeric ${service.latency_ms > service.threshold_ms ? "slow" : ""}">${service.latency_ms != null ? `${number(service.latency_ms)} <span class="inline-unit">ms</span>` : '<span class="time-cell">No sample</span>'}</td>
    <td class="time-cell" title="${escapeHTML(timestamp(service.checked))}">${service.enabled === false ? "Paused" : age(service.checked)}</td>
    <td><div class="service-controls">${monitoringSwitch(service)}<button class="service-settings-button" data-service-settings="${escapeHTML(service.id)}" aria-label="Configure ${escapeHTML(service.name)}" title="Service details" aria-expanded="${selected}" aria-controls="service-settings">${icon("settings")}</button></div></td>
  </tr>`;
}

function renderServices() {
  const groups = filteredServiceProjects();
  const all = (state.data.projects || []).flatMap((p) => p.services);
  const monitored = all.filter((s) => s.enabled !== false).length;
  const inactive = all.length - monitored;
  const count = groups.reduce((total, p) => total + p.visible.length, 0);
  const pages = Math.max(1, Math.ceil(groups.length / 10));
  state.servicePage = Math.max(0, Math.min(state.servicePage, pages - 1));
  const page = groups.slice(state.servicePage * 10, (state.servicePage + 1) * 10);
  const focused = document.activeElement;
  const hadFocus = $("services-content").contains(focused);
  const monitorContext = focused?.dataset?.monitorContext;
  const focusKey = focused?.dataset?.monitorId || focused?.dataset?.serviceSettings || focused?.dataset?.projectCollapse;
  const focusAttribute = focused?.dataset?.monitorId ? "data-monitor-id" :
    focused?.dataset?.serviceSettings ? "data-service-settings" : "data-project-collapse";
  $("tab-monitored-count").textContent = number(monitored);
  $("tab-inactive-count").textContent = number(inactive);
  document.querySelectorAll('[role="tab"][data-service-tab]').forEach((button) => {
    const selected = button.dataset.serviceTab === state.serviceTab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $("service-list").setAttribute("aria-labelledby", `tab-${state.serviceTab}`);
  $("status-filter").hidden = state.serviceTab === "inactive";
  $("service-tab-description").textContent = state.serviceTab === "inactive"
    ? "Monitoring is off for these services. Enable a service to resume checks."
    : "Services with monitoring enabled. Paused services are in Inactive.";
  $("service-editability").hidden = state.data.selection_editable && !state.failed;
  $("service-editability").textContent = state.failed
    ? "Connection lost. Refresh the dashboard before changing monitoring."
    : "Monitoring controls are read-only for this workspace.";
  const collapsed = groups.length > 0 && groups.every((p) => state.collapsedProjects.has(p.id));
  $("collapse-projects").textContent = collapsed ? "Expand all" : "Collapse all";
  $("collapse-projects").disabled = !groups.length;
  $("services-count").textContent = `${number(count)} ${state.serviceTab === "inactive" ? "inactive" : "monitored"} service${count === 1 ? "" : "s"} across ${number(groups.length)} project${groups.length === 1 ? "" : "s"}`;
  $("services-page").textContent = `${state.servicePage + 1} / ${pages}`;
  $("services-prev").disabled = state.servicePage === 0;
  $("services-next").disabled = state.servicePage >= pages - 1;
  $("services-pagination").hidden = pages === 1;
  $("services-table").innerHTML = groups.length
    ? `<div class="table-wrap"><table class="service-management-table"><thead><tr><th scope="col">Service</th><th scope="col">Provider</th><th scope="col">Health</th><th scope="col">Response</th><th scope="col">Last checked</th><th scope="col">Monitoring</th></tr></thead>${page.map((p, index) => {
      const collapsed = state.collapsedProjects.has(p.id);
      const enabledCount = p.services.filter((s) => s.enabled !== false).length;
      return `<tbody class="project-group"><tr class="project-heading-row"><td colspan="6"><div class="project-heading"><button class="project-disclosure" data-project-collapse="${escapeHTML(p.id)}" aria-expanded="${!collapsed}" aria-controls="project-services-${index}">${icon("chevron")}<strong>${escapeHTML(p.name)}</strong><span class="project-count">${p.visible.length} ${state.serviceTab === "inactive" ? "inactive" : "monitored"}</span><span class="project-coverage">${enabledCount} of ${p.services.length} monitored</span></button>${monitoringButton("projects", p.id, p.name, state.serviceTab === "monitored", p.services.length)}</div></td></tr></tbody><tbody id="project-services-${index}" ${collapsed ? "hidden" : ""}>${collapsed ? "" : p.visible.map(serviceRow).join("")}</tbody>`;
    }).join("")}</table></div>`
    : empty(count === 0 && !$("service-search").value && !$("provider-filter").value &&
        (!$("status-filter").value || state.serviceTab === "inactive")
        ? state.serviceTab === "inactive" ? "No inactive services" : "No monitored services" : "No matching services",
      state.serviceTab === "inactive" ? "All configured services may already be monitored. Try another search or provider." : "Try another search or filter, or enable a service from Inactive.") +
      `<div class="service-empty-action"><button class="button button-compact" data-reset-service-filters>Clear filters</button><button class="button button-compact" data-service-tab="${state.serviceTab === "inactive" ? "monitored" : "inactive"}">View ${state.serviceTab === "inactive" ? "Monitored" : "Inactive"}</button></div>`;
  $("selection-feedback").hidden = !state.selectionMessage;
  $("selection-feedback").textContent = state.selectionMessage;
  $("selection-feedback").classList.toggle("is-error", state.selectionError);
  renderServiceSettings();
  if (focusKey && hadFocus) {
    const replacement = [...$("services-content").querySelectorAll(`[${focusAttribute}]`)]
      .find((button) => button.getAttribute(focusAttribute) === focusKey && !button.disabled &&
        (!monitorContext || button.dataset.monitorContext === monitorContext));
    (replacement || $(`tab-${state.serviceTab}`)).focus({ preventScroll: true });
  }
}

function renderServiceSettings() {
  const panel = $("service-settings");
  const project = (state.data.projects || []).find((p) => p.services.some((s) => s.id === state.selectedService));
  const service = project?.services.find((s) => s.id === state.selectedService);
  const same = panel.dataset.selectedServiceId === service?.id;
  const focused = same && panel.contains(document.activeElement) ? document.activeElement?.dataset?.settingsFocus : null;
  panel.hidden = !service;
  $("services-workspace").classList.toggle("has-service-settings", !!service);
  if (!service) { panel.innerHTML = ""; return; }
  const enabled = service.enabled !== false;
  const moved = enabled !== (state.serviceTab === "monitored");
  const incidents = (state.data.incidents || []).filter((i) => i.target === service.id && i.status === "open");
  panel.innerHTML = `<div class="service-settings-heading"><h2>Service details</h2><button class="icon-button" data-close-service-settings data-settings-focus="close" aria-label="Close service details">×</button></div>
    <h3 id="service-settings-title" class="service-settings-title">${escapeHTML(service.name)}</h3><p class="service-settings-project">${escapeHTML(project.name)} · ${escapeHTML(service.environment)}</p>
    <section class="service-setting-section"><div class="service-setting-label"><h3>Monitoring</h3>${monitoringSwitch(service, "details")}</div><p>${enabled ? "Checks run on this service's configured schedule." : "Checks, new alerts, and new automatic repairs are paused. Recorded evidence is retained."}</p>${moved ? `<button class="text-link service-tab-link" data-service-tab="${enabled ? "monitored" : "inactive"}" data-settings-focus="tab">View in ${enabled ? "Monitored" : "Inactive"} ${icon("arrow")}</button>` : ""}</section>
    <section class="service-setting-section"><h3>${enabled ? "Recorded health" : "Monitoring paused"}</h3><div class="service-settings-status">${badge(service.status, enabled ? undefined : "Paused")}</div><dl class="service-settings-facts"><div><dt>Provider</dt><dd>${escapeHTML(service.provider)}</dd></div><div><dt>Environment</dt><dd>${escapeHTML(service.environment)}</dd></div><div><dt>Last checked</dt><dd>${enabled ? escapeHTML(age(service.checked)) : "Paused"}</dd></div><div><dt>Response</dt><dd>${service.latency_ms != null ? `${number(service.latency_ms)} ms` : "No sample"}</dd></div>${enabled ? `<div><dt>Open incidents</dt><dd>${number(service.open_incidents ?? incidents.length)}</dd></div>` : ""}</dl></section>
    <section class="service-setting-section"><h3>Checks &amp; evidence</h3><p>${enabled ? "Inspect recorded observations and related incidents." : "Enable monitoring to collect current readings. Existing records stay saved."}</p><button class="button button-compact" data-service="${escapeHTML(service.id)}" data-settings-focus="evidence">View recorded evidence ${icon("arrow")}</button></section>`;
  panel.dataset.selectedServiceId = service.id;
  if (focused) panel.querySelector(`[data-settings-focus="${focused}"]`)?.focus({ preventScroll: true });
}

function selectServiceTab(tab) {
  if (!["monitored", "inactive"].includes(tab)) return;
  state.serviceTab = tab;
  state.servicePage = 0;
  state.selectedService = null;
  renderServices();
}

function closeServiceSettings() {
  const id = state.selectedService;
  state.selectedService = null;
  renderServices();
  const button = [...document.querySelectorAll("#services-table [data-service-settings]")]
    .find((button) => button.dataset.serviceSettings === id);
  (button || $(`tab-${state.serviceTab}`)).focus({ preventScroll: true });
}

async function changeMonitoring(button) {
  if (state.selectionBusy) return;
  const {
    monitorScope: scope,
    monitorId: id,
    monitorEnabled: value,
    monitorContext: context,
  } = button.dataset;
  state.selectionBusy = `${scope}:${id}`;
  state.selectionMessage = "";
  state.selectionError = false;
  renderServices();
  const pendingFocus = document.activeElement;
  try {
    const result = await requestAction(
      `/api/${scope}/${encodeURIComponent(id)}/monitoring`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-OpsWatch-CSRF":
            document.querySelector('meta[name="opswatch-csrf"]')?.content || "",
        },
        body: JSON.stringify({ enabled: value === "true" }),
        signal: AbortSignal.timeout(10000),
      },
    );
    if (state.refreshPromise) await state.refreshPromise;
    await refresh();
    state.selectionMessage = `${value === "true" ? "Enabled" : "Paused"} monitoring for ${result.targets.length} service${result.targets.length === 1 ? "" : "s"}. ${value === "true" ? "Find enabled services in Monitored. Checks follow each service's configured schedule." : "Find paused services in Inactive. Recorded evidence is retained."}`;
    if (state.failed) {
      state.selectionError = true;
      state.selectionMessage +=
        " The display could not refresh. Press Refresh to reload the saved state.";
    }
  } catch (error) {
    state.selectionError = true;
    state.selectionMessage =
      error.name === "TimeoutError" || error.name === "TypeError"
        ? "Could not confirm the change. Refresh to check the saved monitoring state."
        : error.message;
  } finally {
    const restoreFocus = state.view === "services" && document.activeElement === pendingFocus;
    state.selectionBusy = null;
    renderServices();
    if (restoreFocus) {
      const controls = [...document.querySelectorAll("#services-content [data-monitor-id]")];
      const control = controls.find((b) => b.dataset.monitorId === id && b.dataset.monitorScope === scope &&
        (!context || b.dataset.monitorContext === context) && !b.disabled) ||
        controls.find((b) => b.dataset.monitorId === id && b.dataset.monitorScope === scope && !b.disabled);
      (control || $(`tab-${state.serviceTab}`)).focus({ preventScroll: true });
    }
  }
}

function renderIncidents() {
  const incidents = filteredIncidents();
  const groups = groupIncidents(incidents).sort((a, b) => {
    const rank = (group) => (group.items[0].status === "resolved" ? 10 : 0) +
      (group.items[0].kind === "monitoring" ? 2 : 0) +
      (group.items[0].severity === "critical" ? 0 : 1);
    return rank(a) - rank(b) || b.items[0].updated - a.items[0].updated;
  });
  if (!state.selectedIncident || (!state.pinnedIncident && !incidents.some((i) => i.ref === state.selectedIncident))) {
    state.selectedIncident = groups[0]?.items[0].ref || null;
    state.mobileDetail = false;
  }
  const count = state.data.incidents.filter((i) => i.status === "open").length;
  $("tab-open-count").textContent = number(count);
  $("tab-active-count").textContent = number(state.data.incidents.filter((i) => jobPresentation(latestJob(i.ref)).active).length);
  document.querySelectorAll("[data-incident-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.incidentFilter === $("incident-status").value));
  });
  const history = state.data.incidents.filter((i) => i.status === "resolved").length;
  $("history-caption").textContent = `${number(incidents.length)} matching incidents · ${number(groups.length)} issues · All ${number(count)} open incidents and ${number(history)} recent recovered or quiet records available`;
  const focused = document.activeElement?.dataset?.incident;
  $("incidents-list").innerHTML = groups.slice(0, state.incidentLimit).map((group) => {
    const item = group.items[0];
    const service = state.data.services.find((s) => s.id === item.target);
    const selected = group.items.some((i) => i.ref === state.selectedIncident);
    const job = latestJob(item.ref);
    const label = group.items.length > 1 ? `${service?.provider || "Provider"} collection` : serviceName(item.target);
    const title = group.items.length > 1 ? "Production signals unavailable" : item.title;
    const monitoring = item.kind === "monitoring" && item.status === "open";
    return `<button class="triage-row ${selected ? "is-selected" : ""}" data-incident="${escapeHTML(selected ? state.selectedIncident : item.ref)}" aria-label="View ${escapeHTML(label)} incident ${escapeHTML(item.ref)}" aria-current="${selected ? "true" : "false"}"><span class="issue-symbol ${incidentState(item)}">${icon(monitoring || item.severity === "critical" ? "alert" : "pulse")}</span><span class="triage-copy"><strong>${escapeHTML(label)}</strong><span>${escapeHTML(title)}</span><span class="triage-meta"><span class="mono">${escapeHTML(item.ref)}${group.items.length > 1 ? ` +${group.items.length - 1}` : ""}</span><span>${group.items.length > 1 ? `${group.items.length} affected services` : escapeHTML(item.kind)}</span><time title="${escapeHTML(timestamp(item.updated))}">${age(item.updated)}</time></span></span><span class="triage-status">${job ? badge(jobPresentation(job).tone, jobPresentation(job).label) : badge(monitoring ? "warning" : incidentState(item), monitoring ? "Monitoring gap" : undefined)}</span><span class="row-arrow">${icon("arrow")}</span></button>`;
  }).join("") || empty("No matching incidents", "Change your search or filter to see other recorded evidence.");
  if (groups.length > state.incidentLimit) $("incidents-list").insertAdjacentHTML("beforeend", `<div class="load-more"><button class="button" data-load-incidents>Show more issues</button><span>${state.incidentLimit} of ${groups.length} shown</span></div>`);
  if (focused) document.querySelectorAll("#incidents-list [data-incident]").forEach((button) => {
    if (button.dataset.incident === focused) button.focus({ preventScroll: true });
  });
  $("incidents-content").classList.toggle("show-detail", state.mobileDetail);
  renderIncidentDetail();
}

function selectedIncident() {
  return state.data.incidents.find((i) => i.ref === state.selectedIncident) || state.incidentCache.get(state.selectedIncident);
}

function evidenceHTML(item) {
  const evidence = { ...item.evidence };
  const recovery = evidence.recovery;
  delete evidence.recovery;
  const legacyQuiet = !Object.keys(evidence).length || (Object.keys(evidence).length === 1 && Object.hasOwn(evidence, "quiet_seconds"));
  const lead = evidence.error || evidence.message || evidence.exception || evidence.reason;
  return `${lead && typeof lead === "string" ? `<p class="evidence-lead">${escapeHTML(lead)}</p>` : ""}${legacyQuiet ? '<p class="evidence-warning">The original failure was not retained for this older incident. Inspect provider logs before requesting a repair.</p>' : ""}<pre>${escapeHTML(JSON.stringify(evidence, null, 2))}</pre>${recovery ? `<details data-disclosure="recovery"><summary>Recovery observation</summary><pre>${escapeHTML(JSON.stringify(recovery, null, 2))}</pre></details>` : ""}`;
}

function renderIncidentDetail(force = false) {
  const panel = $("incident-detail");
  const item = selectedIncident();
  if (!item) {
    panel.innerHTML = empty("A clear view of the issue", "Select an incident to inspect its evidence, next step, and repair activity.");
    panel.dataset.version = "";
    return;
  }
  const job = latestJob(item.ref);
  const group = groupIncidents(state.data.incidents).find((g) => g.items.some((i) => i.ref === item.ref));
  const version = JSON.stringify([item, job, group?.items.map((i) => i.ref), state.failed, state.data.monitor.status, state.fixPending.has(item.ref), state.fixErrors.get(item.ref)]);
  if (!force && panel.dataset.version === version) return;
  const same = panel.dataset.ref === item.ref;
  const scroll = same ? panel.scrollTop : 0;
  const disclosures = same ? [...panel.querySelectorAll("details[open]")].map((d) => d.dataset.disclosure) : [];
  const focusKey = panel.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  const quiet = incidentState(item) === "quiet";
  const service = state.data.services.find((s) => s.id === item.target);
  const command = `/diagnose ${item.ref}`;
  panel.innerHTML = `<button class="back-to-incidents button" data-back-incidents>← Back to incidents</button>
    <div class="detail-topline"><span>${escapeHTML(serviceName(item.target))}</span><span class="mono incident-ref">${escapeHTML(item.ref)}</span>${badge(incidentState(item), item.kind === "monitoring" && item.status === "open" ? "Monitoring gap" : undefined)}</div>
    <h2 id="incident-title">${escapeHTML(item.title)}</h2>
    <p class="detail-intro">${item.kind === "monitoring" ? "The monitor could not verify all production signals. This does not establish that the application is down." : quiet ? "Matching events have stopped. Production recovery and a code fix have not been verified." : item.status === "resolved" ? "Recovery was confirmed by repeated checks. Review the recorded observations below." : "Review the recorded evidence and next step before starting a repair."}</p>
    <dl class="incident-facts"><div><dt>First seen</dt><dd>${escapeHTML(timestamp(item.opened))}</dd></div><div><dt>Last observed</dt><dd>${escapeHTML(timestamp(item.updated))}</dd></div><div><dt>Observations</dt><dd>${number(item.occurrences || 1)}</dd></div><div><dt>Source</dt><dd>${escapeHTML(service?.provider || item.kind)} · ${escapeHTML(service?.environment || "production")}</dd></div></dl>
    ${group?.items.length > 1 ? `<section class="detail-section"><h3>Affected services</h3><p>These incidents share a provider account and the same collection error.</p><div class="affected-services">${group.items.map((i) => `<button class="affected-service ${i.ref === item.ref ? "is-selected" : ""}" data-incident="${escapeHTML(i.ref)}" data-focus-key="affected-${escapeHTML(i.ref)}"><span>${escapeHTML(serviceName(i.target))}</span><span class="mono">${escapeHTML(i.ref)}</span></button>`).join("")}</div></section>` : ""}
    <section class="detail-section evidence-section"><div class="section-heading"><h3>${item.evidence?.recovery ? "Failure evidence" : "Recorded evidence"}</h3><span class="muted">${escapeHTML(item.kind)}</span></div>${evidenceHTML(item)}</section>
    <section class="detail-section"><h3>Recommended next step</h3><p>${escapeHTML(item.next_step || "Review the recorded evidence.")}</p>${item.action && item.action !== "No action taken" ? `<p>Recorded action: ${escapeHTML(item.action)}</p>` : ""}<div class="detail-command"><code>${escapeHTML(command)}</code><button class="button button-compact" data-copy="${escapeHTML(command)}" data-focus-key="diagnose">Copy command</button></div><p class="detail-note">Send this command to your Hermes Telegram bot for a diagnosis.</p></section>
    <section id="repair-panel" class="detail-section">${repairDetails(item)}</section>
    <div class="detail-links">${(item.links || []).filter((link) => safeURL(link.url)).map((link) => `<a class="button button-compact" href="${escapeHTML(safeURL(link.url))}" target="_blank" rel="noopener noreferrer">${escapeHTML(link.text)} ↗</a>`).join("")}</div>`;
  panel.dataset.ref = item.ref;
  panel.dataset.version = version;
  panel.scrollTop = scroll;
  panel.querySelectorAll("details").forEach((d) => { d.open = disclosures.includes(d.dataset.disclosure); });
  if (focusKey) panel.querySelectorAll("[data-focus-key]").forEach((button) => {
    if (button.dataset.focusKey === focusKey) button.focus({ preventScroll: true });
  });
}

function renderAgents() {
  const filter = $("agent-status").value || "all";
  const jobs = (state.data.repairs || []).filter((job) =>
    filter === "all" || (filter === "active" ? jobPresentation(job).active :
      filter === "review" ? job.status === "pr_open" :
      filter === "blocked" ? ["blocked", "interrupted"].includes(job.status) :
      ["pr_open", "blocked", "interrupted", "cancelled"].includes(job.status)),
  );
  const worker = workspaceHealth().worker;
  $("agent-caption").textContent = `${jobs.length} matching attempts · ${worker === "running" ? "Worker heartbeat is current" : "Worker is not confirmed online"} · Active attempts and the latest 100 outcomes`;
  const list = $("agent-list");
  const focusKey = list.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  list.innerHTML = jobs.map((job) => {
    const status = jobPresentation(job);
    const item = state.data.incidents.find((i) => i.ref === job.incident);
    const url = safeURL(job.pr_url);
    return `<article class="agent-row"><div class="agent-row-heading"><div><span class="agent-service">${escapeHTML(job.service || serviceName(item?.target))}</span><h3>${escapeHTML(job.title || item?.title || job.incident)}</h3></div>${badge(status.tone, status.label)}</div><div class="agent-row-meta"><span class="mono">${escapeHTML(job.id)}</span><span class="mono">${escapeHTML(job.incident)}</span><span>Updated ${escapeHTML(age(job.updated))}</span></div><p class="agent-summary">${escapeHTML(job.summary || "The worker has not recorded an outcome yet.")}</p>${repairProgress(job)}<div class="agent-actions"><button class="button button-compact" data-incident="${escapeHTML(job.incident)}" data-focus-key="${escapeHTML(job.id)}-incident">Inspect incident</button>${url ? `<a class="button button-primary button-compact" data-focus-key="${escapeHTML(job.id)}-pr" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Review draft PR ↗</a>` : ""}</div></article>`;
  }).join("") || empty("No matching agent work", "Start a fix from a mapped incident to investigate in an isolated workspace and prepare a draft PR. Existing attempts appear here when the worker records them.");
  if (focusKey) list.querySelectorAll("[data-focus-key]").forEach((button) => {
    if (button.dataset.focusKey === focusKey) button.focus({ preventScroll: true });
  });
}

function repairProgress(job) {
  const status = jobPresentation(job);
  return `<ol class="repair-progress" aria-label="Repair progress">${["Workspace", "Investigate & fix", "Validate", "Draft PR"].map((label, index) => `<li class="${index < status.phase ? "is-complete" : index === status.phase ? "is-current" : ""}"${index === status.phase ? ' aria-current="step"' : ""}><span class="step-number">${index < status.phase ? "✓" : index + 1}</span><span>${label}</span></li>`).join("")}</ol>`;
}

function renderQueries() {
  $("queries-list").innerHTML =
    state.data.queries
      .slice(0, state.queryLimit)
      .map(
        (q) =>
          `<article class="query-card"><div class="query-heading"><strong>${escapeHTML(q.name)}</strong>${badge(q.stale || state.data.monitor.status !== "running" ? "stale" : q.healthy ? "healthy" : "warning")}</div><div class="query-meta"><span>Interval average <strong>${typeof q.evidence.interval_mean_ms === "number" ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(q.evidence.interval_mean_ms) + " ms" : "No sample"}</strong></span><span>Calls <strong>${number(q.evidence.interval_calls || 0)}</strong></span><span>Recorded ${escapeHTML(timestamp(q.checked))}</span></div><details><summary>View SQL · ${escapeHTML(q.name)} · ${escapeHTML((q.evidence.query || "No SQL").slice(0, 60))}</summary><pre>${escapeHTML(q.evidence.query || "No SQL text recorded.")}</pre></details></article>`,
      )
      .join("") ||
    empty(
      "No query samples recorded",
      "Enabled database targets report interval averages once enough query calls have been observed.",
    );
  if (state.data.queries.length > state.queryLimit)
    $("queries-list").insertAdjacentHTML(
      "beforeend",
      `<div class="load-more"><button class="button" data-load-queries>Show more queries</button><span>${Math.min(state.queryLimit, state.data.queries.length)} of ${state.data.queries.length} recorded samples shown</span></div>`,
    );
}

function navigate() {
  const previousView = state.view;
  const name = location.hash.slice(1);
  state.view = Object.hasOwn(views, name) ? name : "overview";
  if (previousView !== state.view) window.scrollTo({ top: 0 });
  document.body.dataset.view = state.view;
  const [title, subtitle] = views[state.view];
  $("page-title").textContent = title;
  $("breadcrumb-page").textContent = title;
  $("page-subtitle").textContent = subtitle;
  document.title = `OpsWatch · ${title}`;
  for (const view of Object.keys(views)) {
    $(`${view}-content`).hidden = view !== state.view;
    const link = document.querySelector(`[data-view="${view}"]`);
    link.classList.toggle("active", view === state.view);
    if (view === state.view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (state.data) renderCurrent();
}

function renderCurrent() {
  renderMonitor();
  if (state.view === "overview") renderOverview();
  if (state.view === "services") renderServices();
  if (state.view === "incidents") renderIncidents();
  if (state.view === "queries") renderQueries();
  if (state.view === "agents") renderAgents();
}

function showDialog(content, type, id) {
  state.dialog = { type, id };
  $("detail-content").innerHTML = content;
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
}

function showService(id) {
  const service =
    state.data.services.find((s) => s.id === id) ||
    state.data.projects?.flatMap((p) => p.services).find((s) => s.id === id);
  if (!service) return;
  const incidents = state.data.incidents.filter(
    (i) => i.target === id && i.status === "open",
  );
  const url = safeURL(service.url);
  showDialog(
    `${badge(service.status)}<h2 id="detail-title">${escapeHTML(service.name)}</h2><p>${escapeHTML(service.provider)} · ${escapeHTML(service.environment)} · Last checked ${escapeHTML(timestamp(service.checked))}</p>${url ? `<div class="detail-links"><a class="button" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Open app ↗</a></div>` : ""}${incidents.length ? `<div class="detail-section"><h3>Open incidents</h3>${incidents.map((i) => `<button class="incident-card" data-incident="${escapeHTML(i.ref)}"><span class="mono">${escapeHTML(i.ref)}</span><p>${escapeHTML(i.title)}</p></button>`).join("")}</div>` : ""}<div class="detail-section"><h3>Recorded checks</h3>${service.checks.map((check) => `<div class="check-item"><div class="check-title"><h3>${escapeHTML(check.title)}</h3>${badge(check.stale || state.data.monitor.status !== "running" ? "stale" : check.healthy === null ? "unknown" : check.healthy ? "healthy" : "warning")}</div><details><summary>${escapeHTML(check.kind)} · ${escapeHTML(timestamp(check.checked))} · Show evidence</summary><pre>${escapeHTML(JSON.stringify(check.evidence, null, 2))}</pre></details></div>`).join("") || empty("No recorded checks", "The monitor has not collected this target yet.")}</div>`,
    "service",
    id,
  );
}

function safeURL(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function repairDetails(item) {
  const job = latestJob(item.ref);
  const status = jobPresentation(job);
  const pending = state.fixPending.has(item.ref);
  const available = canStartRepair(item, job);
  const url = safeURL(job?.pr_url);
  const message = job ? `${job.id} · ${status.label}. ${job.summary || "The worker will record its progress here."}` :
    state.failed || state.data.monitor.status !== "running" ? "Fresh monitor readings are required before starting a new fix. Refresh after monitoring reconnects." :
    !Object.keys(item.evidence || {}).length || (Object.keys(item.evidence).length === 1 && Object.hasOwn(item.evidence, "quiet_seconds")) ? "The original failure evidence is unavailable. Use Hermes to inspect provider logs before requesting a repair." :
    item.repair?.available ? "Investigate in an isolated coding workspace, run checks, and prepare a draft PR for your review." :
    item.repair?.reason || "No verified repair mapping is available for this incident.";
  return `<div class="section-heading"><h3>${job ? "Repair activity" : "AI-assisted repair"}</h3>${job ? badge(status.tone, status.label) : ""}</div><p role="status">${escapeHTML(message)}</p>${job ? repairProgress(job) : ""}<div class="detail-links">${available ? `<button class="button button-primary" data-start-fix="${escapeHTML(item.ref)}" data-focus-key="start-fix" ${pending ? 'disabled aria-busy="true"' : ""}>${pending ? "Starting fix…" : job ? "Retry fix" : "Start fix"}</button>` : ""}${url ? `<a class="button button-primary" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Review draft PR ↗</a>` : ""}${job ? '<a class="button" href="#agents">All agent work</a>' : ""}</div>${state.fixErrors.has(item.ref) ? `<p class="repair-error" role="alert">${escapeHTML(state.fixErrors.get(item.ref))}</p>` : ""}${available || status.active ? `<p class="repair-note">Up to ${number(state.data.automation?.daily_limit || 4)} attempts per day. Fixes open a draft PR; they do not merge or deploy.</p>` : ""}`;
}

function updateRepairPanel() {
  if (state.view === "incidents") renderIncidentDetail(true);
  else if (state.view === "agents") renderAgents();
}

async function startFix(ref) {
  if (state.fixPending.has(ref)) return;
  const item = state.data.incidents.find((i) => i.ref === ref) || state.incidentCache.get(ref);
  if (!item || !canStartRepair(item, latestJob(ref))) {
    toast("Refresh the incident and check repair availability before starting a fix.");
    return;
  }
  state.fixPending.add(ref);
  state.fixErrors.delete(ref);
  updateRepairPanel();
  try {
    const token = document.querySelector('meta[name="opswatch-csrf"]')?.content;
    if (!token || token === "__OPSWATCH_CSRF__")
      throw new Error("Reload the dashboard before starting a fix.");
    const job = await requestAction(
      `/api/incidents/${encodeURIComponent(ref)}/fix`,
      {
        method: "POST",
        headers: { "X-OpsWatch-CSRF": token },
        signal: AbortSignal.timeout(10000),
      },
    );
    state.data.repairs = [
      job,
      ...(state.data.repairs || []).filter((r) => r.id !== job.id),
    ];
    toast(`${job.id} · ${job.status.replaceAll("_", " ")}`);
  } catch (error) {
    state.fixErrors.set(
      ref,
      error.name === "TimeoutError" || error.name === "TypeError"
        ? "Could not confirm the repair request. Refresh to check its status before trying again."
        : error.message,
    );
  } finally {
    state.fixPending.delete(ref);
    renderSummary();
    if (state.view === "incidents") renderIncidents();
    else updateRepairPanel();
  }
}

async function requestAction(path, options) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...options.headers,
      "X-OpsWatch-Request-ID": crypto.randomUUID(),
    },
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "The request could not be completed.");
  if (!result.command_id) return result;
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const status = await fetch(
      `/api/commands/${encodeURIComponent(result.command_id)}`,
      {
        cache: "no-store",
        signal: AbortSignal.timeout(7000),
      },
    );
    const command = await status.json();
    if (!status.ok)
      throw new Error(command.error || "Could not read request progress.");
    if (command.state === "done") return command.result;
    if (["error", "expired"].includes(command.state))
      throw new Error(
        command.error ||
          "The request expired before your computer processed it.",
      );
  }
  throw new Error(
    "Still waiting for your computer. Refresh to check the saved state before trying again.",
  );
}

async function showIncident(ref) {
  let item = state.data.incidents.find((i) => i.ref === ref);
  if (!item) {
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(ref)}`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Unavailable");
      item = await response.json();
      if (item.ref !== ref) throw new Error("Invalid incident");
      state.incidentCache.set(ref, item);
    } catch {
      toast("This incident is not in the current snapshot. Check the local dashboard for its recorded evidence.");
      return;
    }
  }
  if ($("detail-dialog").open) $("detail-dialog").close();
  state.selectedIncident = ref;
  state.pinnedIncident = !state.data.incidents.some((i) => i.ref === ref) || !filteredIncidents().some((i) => i.ref === ref);
  state.mobileDetail = true;
  if (state.view !== "incidents") {
    location.hash = "incidents";
    navigate();
  } else renderIncidents();
  if (window.matchMedia("(max-width: 1050px)").matches) {
    $("incident-detail").focus({ preventScroll: true });
    $("incidents-content").scrollIntoView({ block: "start" });
  }
}

async function copyCommand(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast("Diagnosis command copied");
  } catch {
    toast("Copy the displayed command and send it to Hermes.");
  }
}

let toastTimer;
function toast(text) {
  clearTimeout(toastTimer);
  $("toast").textContent = text;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 3500);
}

function refresh() {
  if (state.refreshPromise) return state.refreshPromise;
  state.refreshPromise = loadSnapshot().finally(() => {
    state.refreshPromise = null;
  });
  return state.refreshPromise;
}

async function loadSnapshot() {
  if (state.refreshing) return;
  state.refreshing = true;
  $("refresh").disabled = true;
  try {
    const response = await fetch("/api/snapshot", {
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Snapshot unavailable");
    const data = await response.json();
    if (!Array.isArray(data.services) || !data.summary || !data.monitor)
      throw new Error("Invalid snapshot");
    state.data = data;
    state.failed = false;
    $("connection-error").hidden = true;
    $("loading").hidden = true;
    $("content").hidden = false;
    const selected = $("provider-filter").value;
    $("provider-filter").innerHTML =
      '<option value="">All providers</option>' +
      [
        ...new Set(
          (data.projects?.flatMap((p) => p.services) || data.services).map(
            (s) => s.provider,
          ),
        ),
      ]
        .sort()
        .map(
          (provider) =>
            `<option value="${escapeHTML(provider)}">${escapeHTML(provider)}</option>`,
        )
        .join("");
    $("provider-filter").value = selected;
    renderSummary();
    renderCurrent();
    if (state.dialog && $("detail-dialog").open) {
      updateRepairPanel();
      // Preserve scroll position and focus in an open evidence panel during refresh.
      $("detail-label").textContent = "RECORDED EVIDENCE · REFRESHED ON REOPEN";
    }
  } catch {
    state.failed = true;
    $("loading").hidden = true;
    $("connection-error").textContent = state.data
      ? "The dashboard could not refresh. The readings below are from the last received snapshot."
      : "Unable to read the monitor. Check that the local dashboard is running, then press Refresh.";
    $("connection-error").hidden = false;
    if (state.data) {
      renderSummary();
      updateRepairPanel();
      if (state.view === "services") renderServices();
    }
    else {
      $("monitor-state").textContent = "Dashboard disconnected";
      $("monitor-time").textContent = "No snapshot received";
    }
  } finally {
    state.refreshing = false;
    $("refresh").disabled = false;
  }
}

document.addEventListener("click", (event) => {
  const serviceTab = event.target.closest("[data-service-tab]");
  if (serviceTab) {
    selectServiceTab(serviceTab.dataset.serviceTab);
    if (serviceTab.getAttribute("role") !== "tab") $(`tab-${state.serviceTab}`).focus({ preventScroll: true });
  }
  const project = event.target.closest("[data-project-collapse]");
  if (project) {
    const id = project.dataset.projectCollapse;
    if (state.collapsedProjects.has(id)) state.collapsedProjects.delete(id);
    else state.collapsedProjects.add(id);
    renderServices();
  }
  const settings = event.target.closest("[data-service-settings]");
  if (settings) {
    state.selectedService = settings.dataset.serviceSettings;
    renderServices();
    $("service-settings").focus({ preventScroll: true });
  }
  if (event.target.closest("[data-close-service-settings]")) closeServiceSettings();
  if (event.target.closest("[data-reset-service-filters]")) {
    for (const id of ["service-search", "provider-filter", "status-filter"]) $(id).value = "";
    state.servicePage = 0;
    renderServices();
    $("service-search").focus({ preventScroll: true });
  }
  if (event.target.closest("[data-load-queries]")) {
    state.queryLimit += 20;
    renderQueries();
  }
  if (event.target.closest("[data-load-incidents]")) {
    state.incidentLimit += 30;
    renderIncidents();
  }
  const filter = event.target.closest("[data-incident-filter]");
  if (filter) {
    $("incident-status").value = filter.dataset.incidentFilter;
    state.pinnedIncident = false;
    state.selectedIncident = null;
    state.incidentLimit = 30;
    renderIncidents();
  }
  if (event.target.closest("[data-back-incidents]")) {
    state.mobileDetail = false;
    $("incidents-content").classList.remove("show-detail");
    document.querySelectorAll("#incidents-list [data-incident]").forEach((button) => {
      if (button.dataset.incident === state.selectedIncident) button.focus({ preventScroll: true });
    });
  }
  const service = event.target.closest("[data-service]");
  const incident = event.target.closest("[data-incident]");
  const copy = event.target.closest("[data-copy]");
  const fix = event.target.closest("[data-start-fix]");
  const monitoring = event.target.closest("[data-monitor-scope]");
  if (service) showService(service.dataset.service);
  if (incident) showIncident(incident.dataset.incident);
  if (copy) copyCommand(copy.dataset.copy);
  if (fix) startFix(fix.dataset.startFix);
  if (monitoring) changeMonitoring(monitoring);
});
$("refresh").addEventListener("click", refresh);
$("collapse-projects").addEventListener("click", () => {
  const groups = filteredServiceProjects();
  const expand = groups.every((p) => state.collapsedProjects.has(p.id));
  for (const project of groups) {
    if (expand) state.collapsedProjects.delete(project.id);
    else state.collapsedProjects.add(project.id);
  }
  renderServices();
});
for (const [id, delta] of [["services-prev", -1], ["services-next", 1]]) {
  $(id).addEventListener("click", () => {
    state.servicePage += delta;
    renderServices();
    // The page control can become disabled on the first or last page.
    ($(id).disabled ? $(delta > 0 ? "services-prev" : "services-next") : $(id)).focus({ preventScroll: true });
  });
}
document.addEventListener("keydown", (event) => {
  const tab = event.target.closest('[role="tab"][data-service-tab]');
  if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    selectServiceTab(event.key === "Home" ? "monitored" : event.key === "End" ? "inactive" :
      state.serviceTab === "monitored" ? "inactive" : "monitored");
    $(`tab-${state.serviceTab}`).focus({ preventScroll: true });
  }
  if (event.key === "Escape" && state.view === "services" && state.selectedService && !$("detail-dialog").open)
    closeServiceSettings();
});
$("close-dialog").addEventListener("click", () => $("detail-dialog").close());
$("detail-dialog").addEventListener("close", () => {
  state.dialog = null;
  $("detail-label").textContent = "RECORDED EVIDENCE";
});
$("detail-dialog").addEventListener("click", (event) => {
  if (event.target === $("detail-dialog")) {
    const rect = $("detail-dialog").getBoundingClientRect();
    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    )
      $("detail-dialog").close();
  }
});
for (const id of ["service-search", "provider-filter", "status-filter"])
  $(id).addEventListener(id === "service-search" ? "input" : "change", () => {
    state.servicePage = 0;
    state.selectedService = null;
    if (state.data) renderServices();
  });
for (const id of ["incident-search", "incident-status"])
  $(id).addEventListener(id === "incident-search" ? "input" : "change", () => {
    if (state.data) {
      state.pinnedIncident = false;
      state.selectedIncident = null;
      state.incidentLimit = 30;
      renderIncidents();
    }
  });
$("agent-status").addEventListener("change", () => { if (state.data) renderAgents(); });
window.addEventListener("hashchange", navigate);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
setInterval(() => {
  if (!document.hidden) refresh();
}, 30000);
navigate();
refresh();

// Preserve the initials while site icons load, or whenever a site has no usable icon.
document.addEventListener(
  "load",
  (event) => {
    if (
      event.target instanceof HTMLImageElement &&
      event.target.hasAttribute("data-favicon")
    ) {
      event.target.parentElement.classList.add("has-favicon");
    }
  },
  true,
);
document.addEventListener(
  "error",
  (event) => {
    if (
      event.target instanceof HTMLImageElement &&
      event.target.hasAttribute("data-favicon")
    ) {
      event.target.parentElement.classList.remove("has-favicon");
      event.target.remove();
    }
  },
  true,
);
