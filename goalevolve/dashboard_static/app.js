const state = {
  snapshot: null,
  selectedRound: null,
  metric: "goal_distance",
  refreshTimer: null,
};

const metricMeta = {
  goal_distance: { label: "Goal distance", unit: "", field: "goal_distance" },
  tns_abs_ns: { label: "TNS", unit: "ns", field: "tns_abs_ns" },
  dynamic_power_pw: { label: "Dynamic power", unit: "pW", field: "dynamic_power_pw" },
  leakage_power_pw: { label: "Leakage power", unit: "pW", field: "leakage_power_pw" },
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function number(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function metricValue(row, metric) {
  return metric === "goal_distance" ? row.goal_distance : row.metrics?.[metric];
}

function metricUnit(metric) {
  return metricMeta[metric]?.unit || "";
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (["validated", "completed", "baseline", "measured"].includes(value)) return "success";
  if (["failed", "refuted"].includes(value)) return "danger";
  if (["running", "planning", "evaluating"].includes(value)) return "active";
  return "neutral";
}

function statusTag(status) {
  return `<span class="statusTag ${statusClass(status)}">${escapeHtml(status || "unknown")}</span>`;
}

function renderSummary(snapshot) {
  $("campaignTitle").textContent = `${snapshot.design} / ${snapshot.campaign}`;
  $("campaignSubtitle").textContent = "Read-only view of persisted Teacher plans, Student records, and verified QoR evidence.";
  $("campaignStatus").innerHTML = statusTag(snapshot.campaign_status);
  $("currentParent").textContent = snapshot.current_parent.parent_id || "-";
  $("currentDistance").textContent = number(snapshot.current_parent.goal_distance, 6);
  $("roundCount").textContent = String(snapshot.rounds.length);
  const verified = snapshot.qor_history.filter((row) => row.kind !== "baseline" && row.valid).length;
  $("verifiedCount").textContent = String(verified);
}

function renderContract(snapshot) {
  $("contractId").textContent = snapshot.contract.contract_id;
  $("contractList").innerHTML = snapshot.contract.metrics.map((metric) => {
    const direction = metric.minimize ? "at most" : "at least";
    const unit = metricUnit(metric.name);
    return `
      <div class="contractMetric">
        <div><strong>${escapeHtml(metricMeta[metric.name]?.label || metric.name)}</strong><span>Baseline ${number(metric.baseline)} ${unit}</span></div>
        <div class="targetValue"><span>${direction}</span><strong>${number(metric.target)} ${unit}</strong></div>
      </div>`;
  }).join("");
}

function chartMetrics(snapshot) {
  const supported = ["goal_distance", ...snapshot.contract.metrics.map((metric) => metric.name)];
  return [...new Set(supported)].filter((metric) => metricMeta[metric]);
}

function renderMetricTabs(snapshot) {
  const available = chartMetrics(snapshot);
  if (!available.includes(state.metric)) state.metric = "goal_distance";
  $("metricTabs").innerHTML = available.map((metric) => `
    <button type="button" class="metricTab ${metric === state.metric ? "selected" : ""}" data-metric="${metric}">
      ${escapeHtml(metricMeta[metric].label)}
    </button>`).join("");
  document.querySelectorAll(".metricTab").forEach((button) => {
    button.addEventListener("click", () => {
      state.metric = button.dataset.metric;
      renderChart(snapshot);
      renderMetricTabs(snapshot);
    });
  });
}

function svgNode(name, attrs = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text) node.textContent = text;
  return node;
}

function renderChart(snapshot) {
  const metric = state.metric;
  const meta = metricMeta[metric];
  const rows = snapshot.qor_history.filter((row) => metricValue(row, metric) !== null && metricValue(row, metric) !== undefined);
  const svg = $("qorChart");
  const width = 760;
  const height = 290;
  const margin = { top: 20, right: 24, bottom: 42, left: 72 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  svg.replaceChildren();
  $("chartTitle").textContent = meta.label;
  $("chartLegend").innerHTML = `<span><i class="dot measured"></i> Candidate</span><span><i class="line frontier"></i> Valid frontier</span><span><i class="line target"></i> Target</span>`;
  if (!rows.length) {
    svg.appendChild(svgNode("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "svgEmpty" }, "No QoR points recorded yet"));
    return;
  }

  const values = rows.map((row) => Number(metricValue(row, metric)));
  const target = metric === "goal_distance" ? 0 : snapshot.contract.metrics.find((item) => item.name === metric)?.target;
  if (target !== undefined && target !== null) values.push(Number(target));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = Math.max((max - min) * 0.12, Math.abs(max || 1) * 0.05, 1e-9);
  const yMin = min - padding;
  const yMax = max + padding;
  const maxRound = Math.max(...rows.map((row) => Number(row.round)), 1);
  const x = (round) => margin.left + (Number(round) / maxRound) * innerWidth;
  const y = (value) => margin.top + ((yMax - Number(value)) / (yMax - yMin)) * innerHeight;

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = yMin + ((yMax - yMin) * tick) / 4;
    const yy = y(value);
    svg.appendChild(svgNode("line", { x1: margin.left, y1: yy, x2: width - margin.right, y2: yy, class: "gridLine" }));
    svg.appendChild(svgNode("text", { x: margin.left - 10, y: yy + 4, "text-anchor": "end", class: "axisLabel" }, number(value, 4)));
  }
  for (let round = 0; round <= maxRound; round += 1) {
    const xx = x(round);
    svg.appendChild(svgNode("line", { x1: xx, y1: margin.top, x2: xx, y2: height - margin.bottom, class: "gridLine vertical" }));
    svg.appendChild(svgNode("text", { x: xx, y: height - 17, "text-anchor": "middle", class: "axisLabel" }, round === 0 ? "p0" : `R${round}`));
  }
  if (target !== undefined && target !== null) {
    const yy = y(target);
    svg.appendChild(svgNode("line", { x1: margin.left, y1: yy, x2: width - margin.right, y2: yy, class: "targetLine" }));
    svg.appendChild(svgNode("text", { x: width - margin.right, y: yy - 7, "text-anchor": "end", class: "targetLabel" }, `target ${number(target, 4)}`));
  }
  const frontier = [];
  let best = null;
  rows.forEach((row) => {
    if (!row.valid) return;
    const value = Number(metricValue(row, metric));
    const minimize = metric === "goal_distance" || snapshot.contract.metrics.find((item) => item.name === metric)?.minimize !== false;
    if (best === null || (minimize ? value < best.value : value > best.value)) best = { round: row.round, value };
    frontier.push({ round: row.round, value: best.value });
  });
  if (frontier.length > 1) {
    svg.appendChild(svgNode("polyline", { points: frontier.map((point) => `${x(point.round)},${y(point.value)}`).join(" "), class: "frontierLine" }));
  }
  rows.forEach((row) => {
    const value = metricValue(row, metric);
    const circle = svgNode("circle", { cx: x(row.round), cy: y(value), r: row.kind === "baseline" ? 5 : 4.5, class: `point ${row.valid ? "valid" : "invalid"}` });
    const title = svgNode("title", {}, `${row.result_id}: ${number(value, 6)} ${meta.unit}`);
    circle.appendChild(title);
    svg.appendChild(circle);
  });
  svg.appendChild(svgNode("text", { x: 15, y: 16, class: "axisTitle" }, meta.unit ? `${meta.label} (${meta.unit})` : meta.label));
}

function selectedRound(snapshot) {
  const selected = snapshot.rounds.find((round) => round.round_id === state.selectedRound);
  return selected || snapshot.rounds[snapshot.rounds.length - 1] || null;
}

function renderRoundList(snapshot) {
  const current = selectedRound(snapshot);
  if (!current) {
    $("roundList").innerHTML = '<p class="empty">No rounds have been created.</p>';
    return;
  }
  state.selectedRound = current.round_id;
  $("roundList").innerHTML = snapshot.rounds.map((round) => {
    const selected = round.round_id === state.selectedRound;
    const teacher = round.teacher || {};
    return `<button type="button" class="roundButton ${selected ? "selected" : ""}" data-round="${round.round_id}">
      <span class="roundName">Round ${String(round.round).padStart(3, "0")}</span>
      ${statusTag(round.status)}
      <span class="roundDetail">${escapeHtml(teacher.dominant_bottleneck || "Awaiting diagnosis")}</span>
    </button>`;
  }).join("");
  document.querySelectorAll(".roundButton").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedRound = button.dataset.round;
      renderRoundList(snapshot);
      renderRoundDetail(snapshot);
    });
  });
}

function renderRoundDetail(snapshot) {
  const round = selectedRound(snapshot);
  if (!round) {
    $("selectedRoundTitle").textContent = "Teacher ideas";
    $("ideaList").innerHTML = '<p class="empty">No round data is available.</p>';
    $("studentTable").innerHTML = "";
    return;
  }
  $("selectedRoundTitle").textContent = `Round ${String(round.round).padStart(3, "0")} teacher ideas`;
  $("selectedRoundStatus").outerHTML = statusTag(round.status).replace("<span", '<span id="selectedRoundStatus"');
  const teacher = round.teacher || {};
  $("roundBottleneck").textContent = teacher.dominant_bottleneck ? `Dominant bottleneck: ${teacher.dominant_bottleneck}${teacher.responsible_stage ? ` at ${teacher.responsible_stage}` : ""}` : "No bottleneck diagnosis recorded yet.";
  const ideas = teacher.ideas || [];
  $("ideaList").innerHTML = ideas.length ? ideas.map((idea) => `
    <article class="ideaCard">
      <div class="ideaHeader"><strong>${escapeHtml(idea.student_id || "Unassigned")}</strong><span>${escapeHtml(idea.student_role || "explorer")}</span><span>${escapeHtml(idea.hypothesis_id || "planned idea")}</span></div>
      <p>${escapeHtml(idea.claim || "No claim recorded.")}</p>
      <div class="ideaMeta"><span>${escapeHtml((idea.source_hooks || []).join(", ") || "No source hook")}</span><span>${escapeHtml(idea.timing_recipe_id || "No recipe")}</span><span>${escapeHtml(idea.role_mode || "fresh_exploration")}</span><span>${escapeHtml((idea.epd_record_ids || []).join(", ") || "no EPD reference")}</span></div>
    </article>`).join("") : '<p class="empty">The Teacher has not persisted an idea for this round.</p>';
  $("studentTable").innerHTML = (round.students || []).length ? round.students.map((student) => {
    const checks = Object.entries(student.checks || {});
    const checkLabel = checks.length ? `${checks.filter(([, passed]) => passed).length}/${checks.length}` : "-";
    return `<tr>
      <td><strong>${escapeHtml(student.student_id)}</strong><span class="cellSub">${escapeHtml(student.hypothesis_id || "No assignment")}</span></td>
      <td>${statusTag(student.status)}</td>
      <td>${checkLabel}</td>
      <td>${number(student.goal_distance, 6)}</td>
      <td>${escapeHtml(student.evidence_state || "-")}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="5" class="emptyCell">No Students have been allocated.</td></tr>';
}

function renderTopResults(snapshot) {
  $("topResultsTable").innerHTML = snapshot.top_results.map((row, index) => `
    <tr>
      <td>${index + 1}</td>
      <td><strong>${escapeHtml(row.result_id)}</strong><span class="cellSub">${escapeHtml(row.evidence_state)}</span></td>
      <td>${number(row.goal_distance, 6)}</td>
      <td>${number(row.metrics?.tns_abs_ns, 3)}</td>
      <td>${number(row.metrics?.dynamic_power_pw, 3)}</td>
      <td>${number(row.metrics?.leakage_power_pw, 3)}</td>
    </tr>`).join("");
}

function render(snapshot) {
  state.snapshot = snapshot;
  renderSummary(snapshot);
  renderContract(snapshot);
  renderMetricTabs(snapshot);
  renderChart(snapshot);
  renderRoundList(snapshot);
  renderRoundDetail(snapshot);
  renderTopResults(snapshot);
}

async function refresh() {
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Dashboard request failed");
    render(data);
  } catch (error) {
    $("campaignSubtitle").textContent = `Dashboard error: ${error.message}`;
  }
}

$("refreshButton").addEventListener("click", refresh);
$("autoRefresh").addEventListener("change", (event) => {
  if (event.target.checked) {
    refresh();
    state.refreshTimer = window.setInterval(refresh, 5000);
  } else {
    window.clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
});

refresh();
state.refreshTimer = window.setInterval(refresh, 5000);
