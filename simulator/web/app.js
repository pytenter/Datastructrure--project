const SVG_NS = "http://www.w3.org/2000/svg";
const MAP_WIDTH = 1000;
const MAP_HEIGHT = 700;
const MAP_PADDING = 42;
const REPLAY_SIM_TIME_TO_MS = 78;

const VEHICLE_COLORS = [
  "#1F7EDD",
  "#FF6A1A",
  "#E84855",
  "#1FB77B",
  "#FFB221",
  "#0F4C81",
  "#FF8C42",
  "#2B66D9",
  "#C44230",
  "#3AAE95"
];

const dom = {
  scaleSelect: document.getElementById("scaleSelect"),
  strategySelect: document.getElementById("strategySelect"),
  seedInput: document.getElementById("seedInput"),
  collabInput: document.getElementById("collabInput"),
  runBtn: document.getElementById("runBtn"),
  compareBtn: document.getElementById("compareBtn"),
  playBtn: document.getElementById("playBtn"),
  pauseBtn: document.getElementById("pauseBtn"),
  stepBtn: document.getElementById("stepBtn"),
  resetBtn: document.getElementById("resetBtn"),
  statusBar: document.getElementById("statusBar"),
  summaryBox: document.getElementById("summaryBox"),
  metricsList: document.getElementById("metricsList"),
  vehicleStatusList: document.getElementById("vehicleStatusList"),
  taskListBox: document.getElementById("taskListBox"),
  rankingList: document.getElementById("rankingList"),
  logBox: document.getElementById("logBox"),
  benchmarkDataset: document.getElementById("benchmarkDataset"),
  benchmarkScenario: document.getElementById("benchmarkScenario"),
  benchmarkMetric: document.getElementById("benchmarkMetric"),
  refreshBenchmarkBtn: document.getElementById("refreshBenchmarkBtn"),
  benchmarkStatus: document.getElementById("benchmarkStatus"),
  benchmarkChart: document.getElementById("benchmarkChart"),
  benchmarkTable: document.getElementById("benchmarkTable"),
  mapSvg: document.getElementById("mapSvg"),
  edgesLayer: document.getElementById("edgesLayer"),
  historyLayer: document.getElementById("historyLayer"),
  tasksLayer: document.getElementById("tasksLayer"),
  stationsLayer: document.getElementById("stationsLayer"),
  depotLayer: document.getElementById("depotLayer"),
  activeRouteLayer: document.getElementById("activeRouteLayer"),
  carLayer: document.getElementById("carLayer")
};

const state = {
  meta: null,
  scenario: null,
  summary: null,
  events: [],
  eventIndex: 0,
  playing: false,
  animating: false,
  scoreAcc: 0,
  taskState: new Map(),
  routeHistory: [],
  projection: null,
  nodeMap: new Map(),
  vehicleState: new Map(),
  currentTime: 0,
  recentTaskIds: new Set(),
  completedTaskIds: new Set(),
  lastVehiclePanelPaintAt: 0,
  activeReplayVehicleIds: new Set(),
  benchmarkData: null,
  initialVehicleState: new Map(),
  timelineMissions: [],
  timelineBreakpoints: [],
  replayEndTime: 0,
  replayRafId: null,
  replayLastFrameTs: 0,
  replayLoggedTaskIds: new Set()
};

window.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  initialize().catch((err) => {
    setStatus(`Init failed: ${err.message}`, true);
  });
});

function bindEvents() {
  dom.runBtn.addEventListener("click", () => void runSimulation());
  dom.compareBtn.addEventListener("click", () => void compareStrategies());
  dom.playBtn.addEventListener("click", () => void playReplay());
  dom.pauseBtn.addEventListener("click", pauseReplay);
  dom.stepBtn.addEventListener("click", () => void stepReplay());
  dom.resetBtn.addEventListener("click", resetReplay);
  dom.refreshBenchmarkBtn.addEventListener("click", () => void loadBenchmarks());
  dom.benchmarkDataset.addEventListener("change", () => {
    renderBenchmarkScenarioOptions();
    renderBenchmarkView();
  });
  dom.benchmarkScenario.addEventListener("change", renderBenchmarkView);
  dom.benchmarkMetric.addEventListener("change", renderBenchmarkView);
}

async function initialize() {
  const meta = await fetchJson("/api/meta");
  state.meta = meta;

  fillSelect(dom.scaleSelect, meta.scales, meta.defaults.scale);
  fillSelect(dom.strategySelect, meta.strategies, meta.defaults.strategy);
  dom.seedInput.value = String(meta.defaults.seed);
  dom.collabInput.checked = Boolean(meta.defaults.allow_collaboration);

  await loadBenchmarks();
  setStatus("Status: choose options and run simulation");
}

function fillSelect(el, values, defaultValue) {
  el.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === defaultValue;
    el.appendChild(option);
  });
}

async function loadBenchmarks() {
  dom.benchmarkStatus.textContent = "Loading benchmark files...";
  try {
    const payload = await fetchJson("/api/benchmarks");
    state.benchmarkData = payload;
    renderBenchmarkControls();
    renderBenchmarkView();
  } catch (err) {
    state.benchmarkData = null;
    dom.benchmarkStatus.textContent = `Failed to load benchmarks: ${err.message}`;
    dom.benchmarkChart.className = "benchmark-chart empty";
    dom.benchmarkChart.textContent = "Benchmark API unavailable.";
    dom.benchmarkTable.innerHTML = "";
  }
}

function renderBenchmarkControls() {
  const datasets = state.benchmarkData?.datasets || [];
  const metrics = state.benchmarkData?.metrics || [];
  const defaults = state.benchmarkData?.defaults || {};

  if (!datasets.length) {
    dom.benchmarkDataset.innerHTML = "";
    dom.benchmarkScenario.innerHTML = "";
    dom.benchmarkMetric.innerHTML = "";
    dom.benchmarkStatus.textContent = "No benchmark summary file found in results/.";
    dom.benchmarkChart.className = "benchmark-chart empty";
    dom.benchmarkChart.textContent = "Run benchmark commands first, then click Refresh Data.";
    dom.benchmarkTable.innerHTML = "";
    return;
  }

  fillSelectByItems(
    dom.benchmarkDataset,
    datasets.map((item) => ({
      value: item.key,
      label: `${item.label} (${item.row_count})`
    })),
    defaults.dataset_key || datasets[0].key
  );

  fillSelectByItems(
    dom.benchmarkMetric,
    metrics.map((item) => ({
      value: item.id,
      label: item.label
    })),
    defaults.metric || "score"
  );
  renderBenchmarkScenarioOptions();
}

function renderBenchmarkScenarioOptions() {
  const dataset = getSelectedBenchmarkDataset();
  if (!dataset) {
    dom.benchmarkScenario.innerHTML = "";
    return;
  }

  const values = Array.from(
    new Set((dataset.rows || []).map((row) => String(row.scenario || "all")))
  ).sort((a, b) => a.localeCompare(b));

  const current = dom.benchmarkScenario.value || "all";
  const options = [{ value: "all", label: "all" }, ...values.map((value) => ({ value, label: value }))];
  fillSelectByItems(dom.benchmarkScenario, options, options.some((item) => item.value === current) ? current : "all");
}

function renderBenchmarkView() {
  const dataset = getSelectedBenchmarkDataset();
  if (!dataset) {
    dom.benchmarkStatus.textContent = "No benchmark dataset selected.";
    dom.benchmarkChart.className = "benchmark-chart empty";
    dom.benchmarkChart.textContent = "No data.";
    dom.benchmarkTable.innerHTML = "";
    return;
  }

  const scenario = dom.benchmarkScenario.value || "all";
  const metricId = dom.benchmarkMetric.value || "score";
  const metricMeta = getBenchmarkMetricMeta(metricId);
  const direction = metricMeta.direction || "desc";

  const filteredRows = (dataset.rows || []).filter((row) => scenario === "all" || row.scenario === scenario);
  if (!filteredRows.length) {
    dom.benchmarkStatus.textContent = `${dataset.label} has no rows for scenario "${scenario}".`;
    dom.benchmarkChart.className = "benchmark-chart empty";
    dom.benchmarkChart.textContent = "No rows available for current filter.";
    dom.benchmarkTable.innerHTML = "";
    return;
  }

  const prepared = prepareBenchmarkRowsForDisplay(dataset, filteredRows, metricId);
  const sorted = [...prepared.rows].sort((a, b) => {
    const va = numberValue(a.__displayMetric);
    const vb = numberValue(b.__displayMetric);
    return direction === "asc" ? va - vb : vb - va;
  });

  const metricValues = sorted.map((row) => numberValue(row.__displayMetric));
  const minVal = Math.min(...metricValues);
  const maxVal = Math.max(...metricValues);
  const span = Math.max(1e-9, maxVal - minVal);

  dom.benchmarkChart.className = "benchmark-chart";
  dom.benchmarkChart.innerHTML = "";
  sorted.forEach((row) => {
    const val = numberValue(row.__displayMetric);
    const ratio =
      direction === "asc"
        ? (maxVal - val) / span
        : (val - minVal) / span;
    const width = 15 + ratio * 85;

    const line = document.createElement("div");
    line.className = "benchmark-row";

    const label = document.createElement("div");
    label.className = "benchmark-label";
    label.textContent = `${row.scenario} | ${cleanBenchmarkLabel(row.strategy)} | ${cleanBenchmarkLabel(row.mode)}`;

    const track = document.createElement("div");
    track.className = "benchmark-track";
    const fill = document.createElement("div");
    fill.className = `benchmark-fill ${String(row.mode).startsWith("static") ? "static" : "dynamic"}`;
    fill.style.width = `${width}%`;
    track.appendChild(fill);

    const value = document.createElement("div");
    value.className = "benchmark-value";
    value.textContent = formatMetricValue(metricId, val);

    line.appendChild(label);
    line.appendChild(track);
    line.appendChild(value);
    dom.benchmarkChart.appendChild(line);
  });

  renderBenchmarkTable(sorted, metricId);
  const updated = dataset.updated_at ? dataset.updated_at.replace("T", " ") : "unknown";
  dom.benchmarkStatus.textContent = `${dataset.label} | file=${dataset.filename} | rows=${filteredRows.length} | updated=${updated}`;
}

function renderBenchmarkTable(rows, metricId) {
  const table = document.createElement("table");
  table.className = "benchmark-table";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Scenario", "Strategy", "Mode", "Metric", "Completed", "Unserved", "Overtime"].forEach((text) => {
    const th = document.createElement("th");
    th.textContent = text;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  rows.slice(0, 20).forEach((row) => {
    const tr = document.createElement("tr");
    const cells = [
      row.scenario,
      cleanBenchmarkLabel(row.strategy),
      cleanBenchmarkLabel(row.mode),
      formatMetricValue(metricId, numberValue(row.__displayMetric)),
      String(Math.round(numberValue(row.__displayCompleted))),
      String(Math.round(numberValue(row.__displayUnserved))),
      String(Math.round(numberValue(row.__displayOvertime)))
    ];
    cells.forEach((text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.appendChild(thead);
  table.appendChild(tbody);
  dom.benchmarkTable.innerHTML = "";
  dom.benchmarkTable.appendChild(table);
}

function fillSelectByItems(el, items, defaultValue) {
  el.innerHTML = "";
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    option.selected = item.value === defaultValue;
    el.appendChild(option);
  });
}

function getSelectedBenchmarkDataset() {
  const datasets = state.benchmarkData?.datasets || [];
  const key = dom.benchmarkDataset.value;
  return datasets.find((item) => item.key === key) || datasets[0] || null;
}

function getBenchmarkMetricMeta(metricId) {
  const metrics = state.benchmarkData?.metrics || [];
  return metrics.find((item) => item.id === metricId) || { id: metricId, label: metricId, direction: "desc" };
}

function prepareBenchmarkRowsForDisplay(_dataset, rows, metricId) {
  return {
    rows: rows.map((row) => ({
      ...row,
      __displayMetric: numberValue(row[metricId]),
      __displayCompleted: Math.max(0, Math.round(numberValue(row.completed))),
      __displayUnserved: Math.max(0, Math.round(numberValue(row.unserved))),
      __displayOvertime: Math.max(0, Math.round(numberValue(row.overtime)))
    }))
  };
}

function numberValue(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function formatMetricValue(metricId, value) {
  if (["completed", "unserved", "overtime"].includes(metricId)) {
    return `${Math.round(value)}`;
  }
  return `${value.toFixed(2)}`;
}

function cleanBenchmarkLabel(value) {
  return String(value ?? "")
    .replace(/reduced/gi, "")
    .replace(/__+/g, "_")
    .replace(/\s{2,}/g, " ")
    .replace(/^[_\-\s|]+/, "")
    .replace(/[_\-\s|]+$/, "");
}

function readCommonPayload() {
  return {
    scale: dom.scaleSelect.value,
    strategy: dom.strategySelect.value,
    seed: Number(dom.seedInput.value),
    allow_collaboration: dom.collabInput.checked
  };
}

async function runSimulation() {
  const payload = readCommonPayload();
  setStatus("Status: simulation is running...");

  try {
    const data = await postJson("/api/run", payload);
    hydrateRunData(data);
    renderStaticMapBase();
    renderReplayAt(0, { appendLogs: false, initialSummary: true });
    dom.logBox.textContent = "";
    setStatus(`Status: simulation complete, ${state.events.length} dispatch events`);
  } catch (err) {
    setStatus(`Status: simulation failed - ${err.message}`, true);
  }
}

function hydrateRunData(data) {
  pauseReplay(true);
  state.scenario = data.scenario;
  state.summary = data.summary;
  state.events = Array.isArray(data.events) ? data.events : [];
  state.eventIndex = 0;
  state.playing = false;
  state.animating = false;
  state.scoreAcc = 0;
  state.routeHistory = [];
  state.currentTime = 0;
  state.recentTaskIds = new Set();
  state.completedTaskIds = new Set(
    state.events.map((event) => Number(event.task_id))
  );
  state.lastVehiclePanelPaintAt = 0;
  state.activeReplayVehicleIds = new Set();
  state.replayLoggedTaskIds = new Set();
  state.replayLastFrameTs = 0;
  state.replayRafId = null;

  state.nodeMap = new Map();
  state.scenario.nodes.forEach((node) => state.nodeMap.set(node.node_id, node));

  state.taskState = new Map();
  state.scenario.tasks.forEach((task) => state.taskState.set(task.task_id, "pending"));

  state.vehicleState = new Map();
  (state.scenario.vehicles || []).forEach((vehicle) => {
    state.vehicleState.set(vehicle.vehicle_id, {
      vehicleId: vehicle.vehicle_id,
      capacity: Number(vehicle.capacity),
      batteryCapacity: Number(vehicle.battery_capacity),
      battery: Number(vehicle.battery),
      status: "idle",
      busyUntil: 0,
      currentNode: vehicle.current_node,
      assignedWeight: 0,
      lastTaskId: null,
      chargeAmount: 0,
      chargeStartTime: null,
      chargeEndTime: null
    });
  });
  state.initialVehicleState = cloneVehicleStateMap(state.vehicleState);

  state.projection = buildProjection(state.scenario.nodes);
  prepareReplayTimeline();
}

async function compareStrategies() {
  const payload = readCommonPayload();
  setStatus("Status: ranking strategies...");

  try {
    const data = await postJson("/api/compare", payload);
    renderRanking(data.ranking || []);
    if (data.ranking && data.ranking.length > 0) {
      const best = data.ranking[0];
      setStatus(`Status: best strategy ${best.strategy}, score ${best.score.toFixed(2)}`);
    } else {
      setStatus("Status: no ranking data returned", true);
    }
  } catch (err) {
    setStatus(`Status: ranking failed - ${err.message}`, true);
  }
}

function renderRanking(ranking) {
  dom.rankingList.innerHTML = "";

  if (!ranking.length) {
    dom.rankingList.className = "ranking-list empty";
    dom.rankingList.textContent = "No data";
    return;
  }

  dom.rankingList.className = "ranking-list";

  const scores = ranking.map((item) => item.score);
  const minScore = Math.min(...scores);
  const maxScore = Math.max(...scores);
  const span = Math.max(1, maxScore - minScore);

  ranking.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "rank-row";

    const order = document.createElement("div");
    order.textContent = String(index + 1);

    const bar = document.createElement("div");
    bar.className = "rank-bar";

    const fill = document.createElement("div");
    fill.className = "rank-fill";
    const width = 18 + ((item.score - minScore) / span) * 82;
    fill.style.width = `${width}%`;

    const label = document.createElement("div");
    label.className = "rank-label";
    label.textContent = `${item.strategy} | completed ${item.completed} | overtime ${item.overtime}`;

    bar.appendChild(fill);
    bar.appendChild(label);

    const score = document.createElement("div");
    score.textContent = item.score.toFixed(1);

    row.appendChild(order);
    row.appendChild(bar);
    row.appendChild(score);

    dom.rankingList.appendChild(row);
  });
}

async function playReplay() {
  if (!state.scenario || !state.events.length) {
    setStatus("Status: run simulation first", true);
    return;
  }
  if (!state.timelineBreakpoints.length || !Number.isFinite(state.replayEndTime)) {
    prepareReplayTimeline();
  }
  if (!state.timelineBreakpoints.length || state.replayEndTime <= 0) {
    setStatus("Status: replay timeline unavailable for current run", true);
    return;
  }
  if (state.playing) {
    return;
  }
  if (state.currentTime >= state.replayEndTime - 1e-9) {
    state.currentTime = 0;
    state.replayLoggedTaskIds = new Set();
    dom.logBox.textContent = "";
    renderReplayAt(0, { appendLogs: false, initialSummary: true });
  }
  if (state.currentTime <= 1e-9) {
    const firstDispatch = (state.timelineMissions || [])
      .map((mission) => numberValue(mission.dispatch))
      .filter((t) => t > 1e-9)
      .sort((a, b) => a - b)[0];
    if (Number.isFinite(firstDispatch)) {
      renderReplayAt(firstDispatch, { appendLogs: false, initialSummary: false });
    }
  }

  state.playing = true;
  state.animating = true;
  state.replayLastFrameTs = 0;
  setStatus("Status: replay in progress...");

  const tick = (ts) => {
    if (!state.playing) {
      state.animating = false;
      state.replayRafId = null;
      return;
    }
    if (!state.replayLastFrameTs) {
      state.replayLastFrameTs = ts;
    }
    const deltaMs = Math.max(0, ts - state.replayLastFrameTs);
    state.replayLastFrameTs = ts;
    const nextSimTime = state.currentTime + deltaMs / Math.max(1e-6, REPLAY_SIM_TIME_TO_MS);
    renderReplayAt(nextSimTime, { appendLogs: true, initialSummary: false });

    if (state.currentTime >= state.replayEndTime - 1e-9) {
      state.playing = false;
      state.animating = false;
      state.replayRafId = null;
      finalizeReplayTaskStates();
      renderSummary(false);
      renderStatusPanels();
      const doneCount = Array.from(state.taskState.values()).filter((s) => s === "done").length;
      setStatus(`Status: replay finished, done ${doneCount}/${state.scenario.tasks.length}`);
      return;
    }
    state.replayRafId = requestAnimationFrame(tick);
  };
  state.replayRafId = requestAnimationFrame(tick);
}

function pauseReplay(silent = false) {
  if (state.replayRafId !== null) {
    cancelAnimationFrame(state.replayRafId);
    state.replayRafId = null;
  }
  state.replayLastFrameTs = 0;
  state.playing = false;
  state.animating = false;
  if (!silent) {
    setStatus("Status: replay paused");
  }
}

async function stepReplay() {
  if (!state.scenario || !state.events.length) {
    return false;
  }
  pauseReplay(true);
  const nextTime = findNextReplayBreakpoint(state.currentTime);
  if (nextTime === null) {
    if (state.currentTime < state.replayEndTime - 1e-9) {
      renderReplayAt(state.replayEndTime, { appendLogs: true, initialSummary: false });
      finalizeReplayTaskStates();
      renderSummary(false);
      renderStatusPanels();
      const doneCount = Array.from(state.taskState.values()).filter((s) => s === "done").length;
      setStatus(`Status: replay finished, done ${doneCount}/${state.scenario.tasks.length}`);
      return true;
    }
    return false;
  }
  renderReplayAt(nextTime, { appendLogs: true, initialSummary: false });
  if (state.currentTime >= state.replayEndTime - 1e-9) {
    finalizeReplayTaskStates();
    renderSummary(false);
    renderStatusPanels();
    const doneCount = Array.from(state.taskState.values()).filter((s) => s === "done").length;
    setStatus(`Status: replay finished, done ${doneCount}/${state.scenario.tasks.length}`);
  } else {
    setStatus(`Status: replay time ${state.currentTime.toFixed(1)}`);
  }
  return true;
}

function resetReplay() {
  if (!state.scenario) {
    return;
  }

  pauseReplay();
  state.eventIndex = 0;
  state.scoreAcc = 0;
  state.routeHistory = [];
  state.currentTime = 0;
  state.recentTaskIds = new Set();
  state.lastVehiclePanelPaintAt = 0;
  state.activeReplayVehicleIds = new Set();
  state.replayLoggedTaskIds = new Set();
  state.scenario.tasks.forEach((task) => state.taskState.set(task.task_id, "pending"));
  restoreVehicleStateToInitial();

  dom.logBox.textContent = "";
  renderStaticMapBase();
  renderReplayAt(0, { appendLogs: false, initialSummary: true });
  setStatus("Status: replay reset");
}

function cloneVehicleStateMap(sourceMap) {
  const out = new Map();
  sourceMap.forEach((value, key) => {
    out.set(key, { ...value });
  });
  return out;
}

function restoreVehicleStateToInitial() {
  state.vehicleState = cloneVehicleStateMap(state.initialVehicleState);
}

function renderStaticMapBase() {
  if (!state.scenario || !state.projection) {
    clearMapLayers();
    return;
  }
  clearMapLayers();
  drawEdges();
  drawStations();
  drawDepot();
}

function prepareReplayTimeline() {
  if (!state.scenario) {
    state.timelineMissions = [];
    state.timelineBreakpoints = [];
    state.replayEndTime = 0;
    return;
  }

  const normalizedEvents = (state.events || [])
    .map((event) => ({
      ...event,
      dispatch_time: numberValue(event.dispatch_time ?? 0),
      completion_time: numberValue(event.completion_time ?? event.dispatch_time ?? 0),
      task_id: Number(event.task_id)
    }))
    .sort((a, b) => {
      const da = Number(a.dispatch_time ?? 0);
      const db = Number(b.dispatch_time ?? 0);
      if (Math.abs(da - db) > 1e-9) {
        return da - db;
      }
      const ca = Number(a.completion_time ?? da);
      const cb = Number(b.completion_time ?? db);
      if (Math.abs(ca - cb) > 1e-9) {
        return ca - cb;
      }
      return Number(a.task_id ?? 0) - Number(b.task_id ?? 0);
    });
  state.events = normalizedEvents;

  const breakpoints = [0];
  const missions = [];
  normalizedEvents.forEach((event) => {
    const dispatch = numberValue(event.dispatch_time ?? 0);
    let completion = numberValue(event.completion_time ?? dispatch + 1e-6);
    if (!Number.isFinite(completion) || completion < dispatch + 1e-6) {
      completion = dispatch + 1e-6;
    }
    breakpoints.push(dispatch, completion);

    const routes = Array.isArray(event.routes) ? event.routes : [];
    routes.forEach((route) => {
      const routeNodes = Array.isArray(route.route_nodes) ? route.route_nodes.map((nodeId) => Number(nodeId)) : [];
      const points = routeToPoints(routeNodes);
      if (!points.length) {
        return;
      }
      const metrics = pathMetrics(points);
      const vehicleId = Number(route.vehicle_id);
      const taskId = Number(route.task_id ?? event.task_id);
      const initialVehicle = state.initialVehicleState.get(vehicleId);
      const routeStartBattery = Number(route.start_battery);
      const startBattery =
        Number.isFinite(routeStartBattery)
          ? routeStartBattery
          : initialVehicle
          ? Number(initialVehicle.battery)
          : Number(route.final_battery ?? 0);
      const finalBattery = numberValue(route.final_battery ?? startBattery);
      const chargeAmount = numberValue(route.charge_amount ?? 0);
      const chargeStartTime = route.charge_start_time == null ? null : Number(route.charge_start_time);
      const chargeEndTime = route.charge_end_time == null ? null : Number(route.charge_end_time);
      const preChargeRatio = estimatePreChargeDistanceRatio(
        {
          ...route,
          route_nodes: routeNodes
        },
        points,
        metrics.total
      );
      const batteryAtTime = buildBatteryInterpolator(
        startBattery,
        finalBattery,
        chargeAmount,
        dispatch,
        completion,
        chargeStartTime,
        chargeEndTime,
        preChargeRatio
      );

      missions.push({
        vehicleId,
        taskId,
        dispatch,
        completion,
        routeNodes,
        points,
        cumulative: metrics.cumulative,
        totalDistancePx: metrics.total,
        batteryAtTime,
        finalBattery,
        finalNode: Number(route.final_node ?? state.scenario.depot_node),
        assignedWeight: Number(route.assigned_weight ?? 0),
        chargeAmount,
        chargeStartTime,
        chargeEndTime
      });
    });
  });

  missions.sort((a, b) => {
    if (Math.abs(a.dispatch - b.dispatch) > 1e-9) {
      return a.dispatch - b.dispatch;
    }
    if (Math.abs(a.completion - b.completion) > 1e-9) {
      return a.completion - b.completion;
    }
    return a.vehicleId - b.vehicleId;
  });

  state.timelineMissions = missions;
  state.timelineBreakpoints = Array.from(
    new Set(
      breakpoints
        .filter((item) => Number.isFinite(item))
        .map((item) => Number(item.toFixed(3)))
    )
  ).sort((a, b) => a - b);
  state.replayEndTime = state.timelineBreakpoints.length ? state.timelineBreakpoints[state.timelineBreakpoints.length - 1] : 0;
}

function findNextReplayBreakpoint(now) {
  const cur = Number(now ?? 0);
  for (const t of state.timelineBreakpoints) {
    if (t > cur + 1e-9) {
      return t;
    }
  }
  return null;
}

function renderReplayAt(simTime, options = {}) {
  if (!state.scenario) {
    return;
  }
  const appendLogs = Boolean(options.appendLogs);
  const initialSummary = Boolean(options.initialSummary);
  const replayEnd = Number.isFinite(state.replayEndTime) ? state.replayEndTime : 0;
  const target = clamp(numberValue(simTime ?? 0), 0, Math.max(0, replayEnd));
  state.currentTime = target;

  const dispatchedEvents = state.events.filter((event) => Number(event.dispatch_time) <= target + 1e-9);
  const completedEvents = state.events.filter((event) => Number(event.completion_time) <= target + 1e-9);
  const activeEvents = state.events.filter(
    (event) => Number(event.dispatch_time) <= target + 1e-9 && Number(event.completion_time) > target + 1e-9
  );
  const dispatchedRoutes = [];
  dispatchedEvents.forEach((event) => {
    const routes = Array.isArray(event.routes) ? event.routes : [];
    routes.forEach((route) => {
      dispatchedRoutes.push({
        vehicle_id: Number(route.vehicle_id),
        route_nodes: Array.isArray(route.route_nodes) ? route.route_nodes : []
      });
    });
  });
  state.routeHistory = dispatchedRoutes.slice(-160);

  const activeMissions = state.timelineMissions.filter(
    (mission) => mission.dispatch <= target + 1e-9 && mission.completion > target + 1e-9
  );
  const finishedMissions = state.timelineMissions.filter((mission) => mission.completion <= target + 1e-9);

  state.scenario.tasks.forEach((task) => state.taskState.set(task.task_id, "pending"));
  activeEvents.forEach((event) => state.taskState.set(Number(event.task_id), "delivering"));
  completedEvents.forEach((event) => state.taskState.set(Number(event.task_id), "done"));
  if (target >= replayEnd - 1e-9) {
    finalizeReplayTaskStates();
  }

  state.recentTaskIds = new Set(activeEvents.map((event) => Number(event.task_id)));
  state.eventIndex = completedEvents.length;
  state.scoreAcc = completedEvents.reduce((acc, item) => acc + Number(item.score || 0), 0);

  restoreVehicleStateToInitial();
  state.activeReplayVehicleIds = new Set();

  finishedMissions.forEach((mission) => {
    const vehicle = state.vehicleState.get(mission.vehicleId);
    if (!vehicle) {
      return;
    }
    vehicle.battery = mission.finalBattery;
    vehicle.currentNode = mission.finalNode;
    vehicle.lastTaskId = mission.taskId;
    vehicle.assignedWeight = 0;
    vehicle.busyUntil = Math.max(Number(vehicle.busyUntil ?? 0), mission.completion);
    vehicle.chargeAmount = 0;
    vehicle.chargeStartTime = null;
    vehicle.chargeEndTime = null;
  });

  const cars = [];
  activeMissions.forEach((mission) => {
    const vehicle = state.vehicleState.get(mission.vehicleId);
    const progress = clamp(
      (target - mission.dispatch) / Math.max(1e-6, mission.completion - mission.dispatch),
      0,
      1
    );
    const dist = mission.totalDistancePx * progress;
    const point = sampleAtDistance(mission.points, mission.cumulative, dist);
    cars.push({ vehicleId: mission.vehicleId, point });

    if (vehicle) {
      vehicle.battery = mission.batteryAtTime(target);
      vehicle.lastTaskId = mission.taskId;
      vehicle.assignedWeight = mission.assignedWeight;
      vehicle.busyUntil = Math.max(Number(vehicle.busyUntil ?? 0), mission.completion);
      vehicle.chargeAmount = mission.chargeAmount;
      vehicle.chargeStartTime = mission.chargeStartTime;
      vehicle.chargeEndTime = mission.chargeEndTime;
    }
    state.activeReplayVehicleIds.add(mission.vehicleId);
  });

  renderReplayLayers(
    activeMissions.map((mission) => ({
      vehicle_id: mission.vehicleId,
      route_nodes: mission.routeNodes
    })),
    cars
  );

  if (appendLogs) {
    completedEvents.forEach((event) => {
      const taskId = Number(event.task_id);
      if (state.replayLoggedTaskIds.has(taskId)) {
        return;
      }
      state.replayLoggedTaskIds.add(taskId);
      appendLog(event);
    });
  }

  renderSummary(initialSummary);
  renderStatusPanels();
}

function renderReplayLayers(activeRoutes, cars) {
  dom.historyLayer.innerHTML = "";
  dom.tasksLayer.innerHTML = "";
  dom.activeRouteLayer.innerHTML = "";
  dom.carLayer.innerHTML = "";
  drawRouteHistory();
  drawTasks();
  drawActiveRoutes(activeRoutes);
  cars.forEach((carInfo) => {
    const car = createCar(carInfo.vehicleId);
    placeCar(car, carInfo.point);
    dom.carLayer.appendChild(car);
  });
}

async function renderScene(activeEvent, animateCars) {
  if (!state.scenario || !state.projection) {
    clearMapLayers();
    return;
  }

  clearMapLayers();
  drawEdges();
  drawRouteHistory();
  drawTasks();
  drawStations();
  drawDepot();

  const activeRoutes = activeEvent && Array.isArray(activeEvent.routes) ? activeEvent.routes : [];
  drawActiveRoutes(activeRoutes);

  if (animateCars && activeRoutes.length) {
    state.animating = true;
    try {
      const dispatchTime = Number(activeEvent?.dispatch_time ?? state.currentTime);
      await animateCarsAlongRoutes(activeRoutes, dispatchTime);
    } finally {
      state.animating = false;
    }
  } else {
    state.animating = false;
    drawRouteEndCars(activeRoutes);
  }
}

function clearMapLayers() {
  dom.edgesLayer.innerHTML = "";
  dom.historyLayer.innerHTML = "";
  dom.tasksLayer.innerHTML = "";
  dom.stationsLayer.innerHTML = "";
  dom.depotLayer.innerHTML = "";
  dom.activeRouteLayer.innerHTML = "";
  dom.carLayer.innerHTML = "";
}

function drawEdges() {
  state.scenario.edges.forEach((edge) => {
    const a = projectNode(edge.a);
    const b = projectNode(edge.b);
    if (!a || !b) {
      return;
    }
    const line = svg("line", {
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      stroke: "#D6DFE7",
      "stroke-width": 1.1
    });
    dom.edgesLayer.appendChild(line);
  });
}

function drawRouteHistory() {
  state.routeHistory.forEach((route) => {
    const points = routeToPoints(route.route_nodes);
    if (points.length < 2) {
      return;
    }

    const polyline = svg("polyline", {
      points: formatPoints(points),
      fill: "none",
      stroke: vehicleColor(route.vehicle_id),
      "stroke-width": 2,
      class: "route-history"
    });
    dom.historyLayer.appendChild(polyline);
  });
}

function drawActiveRoutes(routes) {
  routes.forEach((route) => {
    const points = routeToPoints(route.route_nodes);
    if (points.length < 2) {
      return;
    }

    const polyline = svg("polyline", {
      points: formatPoints(points),
      fill: "none",
      stroke: vehicleColor(route.vehicle_id),
      "stroke-width": 3.2,
      class: "route-active"
    });
    dom.activeRouteLayer.appendChild(polyline);
  });
}

function drawTasks() {
  state.scenario.tasks.forEach((task) => {
    const point = projectNode(task.node_id);
    if (!point) {
      return;
    }

    const status = state.taskState.get(task.task_id) || "pending";
    const isDone = status === "done";
    const isDelivering = status === "delivering";
    const isUnserved = status === "unserved";
    const dot = svg("circle", {
      cx: point.x,
      cy: point.y,
      r: 4.5,
      fill: isDone ? "#1D9B78" : isDelivering ? "#F4A23B" : isUnserved ? "#8C98A4" : "#DF5A67",
      stroke: isDone ? "#126B54" : isDelivering ? "#A76816" : isUnserved ? "#5F6B76" : "#A23B45",
      "stroke-width": 0.7
    });

    dom.tasksLayer.appendChild(dot);
  });
}

function drawStations() {
  state.scenario.stations.forEach((station) => {
    const point = projectNode(station.node_id);
    if (!point) {
      return;
    }
    dom.stationsLayer.appendChild(stationIcon(point.x, point.y, station.station_id));
  });
}

function stationIcon(x, y, stationId) {
  const group = svg("g", { transform: `translate(${x - 9} ${y - 14})` });
  group.appendChild(
    svg("rect", {
      x: 0,
      y: 0,
      width: 18,
      height: 28,
      rx: 4,
      fill: "#FFF4E1",
      stroke: "#DD9B38",
      "stroke-width": 1.3
    })
  );
  group.appendChild(
    svg("path", {
      d: "M10 4 L6 14 H10 L8 24 L13 12 H9 Z",
      fill: "#E28413"
    })
  );
  group.appendChild(
    svg("text", {
      x: 9,
      y: 34,
      "text-anchor": "middle",
      "font-size": 8,
      fill: "#A35E0B"
    }, `S${stationId}`)
  );
  return group;
}

function drawDepot() {
  const point = projectNode(state.scenario.depot_node);
  if (!point) {
    return;
  }

  const group = svg("g", { transform: `translate(${point.x} ${point.y})` });
  group.appendChild(
    svg("path", {
      d: "M-10 5 L0 -10 L10 5 V12 H-10 Z",
      fill: "#1D3557",
      stroke: "#14243D",
      "stroke-width": 1
    })
  );
  group.appendChild(
    svg("rect", {
      x: -3,
      y: 5,
      width: 6,
      height: 7,
      fill: "#D9E6F2"
    })
  );
  group.appendChild(
    svg("text", {
      x: 0,
      y: -14,
      "text-anchor": "middle",
      "font-size": 10,
      fill: "#1D3557",
      "font-weight": 700
    }, "Depot")
  );

  dom.depotLayer.appendChild(group);
}

function drawRouteEndCars(routes) {
  routes.forEach((route) => {
    const points = routeToPoints(route.route_nodes);
    if (!points.length) {
      return;
    }
    const car = createCar(route.vehicle_id);
    placeCar(car, points[points.length - 1]);
    dom.carLayer.appendChild(car);
  });
}

async function animateCarsAlongRoutes(routes, dispatchTime) {
  state.activeReplayVehicleIds = new Set();
  const jobs = routes
    .map((route) => {
      const points = routeToPoints(route.route_nodes);
      if (!points.length) {
        return null;
      }
      return animateSingleCar(route, points, dispatchTime);
    })
    .filter(Boolean);

  await Promise.all(jobs);
  renderVehicleStatuses();
}

async function animateSingleCar(route, points, dispatchTime) {
  const vehicleId = Number(route.vehicle_id);
  const car = createCar(vehicleId);
  dom.carLayer.appendChild(car);
  const vehicle = state.vehicleState.get(vehicleId);
  const routeStartBattery = Number(route.start_battery);
  const startBattery =
    Number.isFinite(routeStartBattery)
      ? routeStartBattery
      : vehicle
      ? Number(vehicle.battery)
      : Number(route.final_battery ?? 0);
  const finalBattery = Number(route.final_battery ?? startBattery);
  const chargeAmount = Number(route.charge_amount ?? 0);
  const dispatch = Number(dispatchTime ?? state.currentTime);
  const completion = Number(route.completion_time ?? dispatch + 1);

  if (points.length === 1) {
    state.activeReplayVehicleIds.delete(vehicleId);
    placeCar(car, points[0]);
    if (vehicle) {
      vehicle.battery = finalBattery;
      vehicle.currentNode = Number(route.final_node ?? vehicle.currentNode);
      renderVehicleStatuses();
    }
    return;
  }

  const metrics = pathMetrics(points);
  const simDuration = Math.max(1, completion - dispatch);
  const durationByTime = simDuration * REPLAY_SIM_TIME_TO_MS;
  const durationByGeometry = metrics.total * 7;
  const durationMs = clamp(Math.max(durationByTime, durationByGeometry), 900, 12000);
  const preChargeRatio = estimatePreChargeDistanceRatio(route, points, metrics.total);
  const batteryAtTime = buildBatteryInterpolator(
    startBattery,
    finalBattery,
    chargeAmount,
    dispatch,
    completion,
    route.charge_start_time == null ? null : Number(route.charge_start_time),
    route.charge_end_time == null ? null : Number(route.charge_end_time),
    preChargeRatio
  );

  state.activeReplayVehicleIds.add(vehicleId);
  renderVehicleStatuses();

  return new Promise((resolve) => {
    const start = performance.now();

    function frame(frameNow) {
      const elapsed = frameNow - start;
      const progress = Math.min(1, elapsed / durationMs);
      const dist = metrics.total * progress;
      const point = sampleAtDistance(points, metrics.cumulative, dist);
      placeCar(car, point);

      if (vehicle) {
        const simTime = dispatch + (completion - dispatch) * progress;
        state.currentTime = Math.max(state.currentTime, simTime);
        vehicle.battery = batteryAtTime(simTime);
        if (frameNow - state.lastVehiclePanelPaintAt > 90) {
          state.lastVehiclePanelPaintAt = frameNow;
          renderVehicleStatuses();
        }
      }

      if (progress < 1) {
        requestAnimationFrame(frame);
      } else {
        state.activeReplayVehicleIds.delete(vehicleId);
        if (vehicle) {
          vehicle.battery = finalBattery;
          vehicle.currentNode = Number(route.final_node ?? vehicle.currentNode);
          state.currentTime = Math.max(state.currentTime, completion);
          renderVehicleStatuses();
        }
        resolve();
      }
    }

    requestAnimationFrame(frame);
  });
}

function createCar(vehicleId) {
  const color = vehicleColor(vehicleId);
  const group = svg("g");

  group.appendChild(
    svg("rect", {
      x: -10,
      y: -8,
      width: 20,
      height: 10,
      rx: 3,
      fill: color,
      stroke: "#0D2035",
      "stroke-width": 0.7
    })
  );
  group.appendChild(
    svg("rect", {
      x: -5,
      y: -13,
      width: 10,
      height: 6,
      rx: 2,
      fill: "#DDE8F2"
    })
  );
  group.appendChild(
    svg("circle", {
      cx: -6,
      cy: 3,
      r: 3,
      fill: "#1A1A1A"
    })
  );
  group.appendChild(
    svg("circle", {
      cx: 6,
      cy: 3,
      r: 3,
      fill: "#1A1A1A"
    })
  );
  group.appendChild(
    svg("text", {
      x: 0,
      y: -15,
      "text-anchor": "middle",
      "font-size": 8,
      fill: "#1B2A3B",
      "font-weight": 700
    }, `V${vehicleId}`)
  );

  return group;
}

function placeCar(car, point) {
  car.setAttribute("transform", `translate(${point.x} ${point.y})`);
}

function estimatePreChargeDistanceRatio(route, points, totalDistance) {
  if (!route || route.station_id == null || !state.scenario || !Array.isArray(route.route_nodes)) {
    return 0.5;
  }
  let stationNodeId = null;
  if (Number(route.station_id) === -1) {
    stationNodeId = Number(state.scenario.depot_node);
  } else {
    const station = (state.scenario.stations || []).find((item) => Number(item.station_id) === Number(route.station_id));
    if (!station) {
      return 0.5;
    }
    stationNodeId = Number(station.node_id);
  }
  const idx = route.route_nodes.findIndex((nodeId) => Number(nodeId) === stationNodeId);
  if (idx <= 0) {
    return 0.5;
  }
  const prefixNodes = route.route_nodes.slice(0, idx + 1);
  const prefixPoints = routeToPoints(prefixNodes);
  const prefixDist = pathMetrics(prefixPoints).total;
  return clamp(prefixDist / Math.max(totalDistance, 1e-6), 0.05, 0.95);
}

function buildBatteryInterpolator(
  startBattery,
  finalBattery,
  chargeAmount,
  dispatchTime,
  completionTime,
  chargeStartTime,
  chargeEndTime,
  preChargeTravelRatio
) {
  const travelEnergy = Math.max(0, startBattery + Math.max(0, chargeAmount) - finalBattery);
  const beforeEnergy = travelEnergy * clamp(preChargeTravelRatio, 0, 1);
  const afterEnergy = Math.max(0, travelEnergy - beforeEnergy);

  const dispatch = Number(dispatchTime);
  const completion = Math.max(dispatch + 1e-6, Number(completionTime));
  const hasChargeWindow =
    chargeAmount > 1e-9 &&
    chargeStartTime !== null &&
    chargeEndTime !== null &&
    Number(chargeStartTime) < Number(chargeEndTime);

  const chargeStart = hasChargeWindow ? Number(chargeStartTime) : null;
  const chargeEnd = hasChargeWindow ? Number(chargeEndTime) : null;

  return (simTime) => {
    const t = clamp(Number(simTime), dispatch, completion);
    if (!hasChargeWindow) {
      const p = (t - dispatch) / Math.max(1e-6, completion - dispatch);
      return startBattery - travelEnergy * p;
    }

    const preEnd = clamp(chargeStart, dispatch, completion);
    const midEnd = clamp(chargeEnd, preEnd, completion);

    if (t <= preEnd + 1e-9) {
      const p = (t - dispatch) / Math.max(1e-6, preEnd - dispatch);
      return startBattery - beforeEnergy * p;
    }

    const afterPre = startBattery - beforeEnergy;
    if (t <= midEnd + 1e-9) {
      const p = (t - preEnd) / Math.max(1e-6, midEnd - preEnd);
      return afterPre + chargeAmount * p;
    }

    const afterCharge = afterPre + chargeAmount;
    const p = (t - midEnd) / Math.max(1e-6, completion - midEnd);
    return afterCharge - afterEnergy * p;
  };
}

function renderSummary(initial) {
  if (!state.summary || !state.scenario) {
    dom.summaryBox.textContent = "Status: Not started";
    dom.metricsList.innerHTML = "";
    return;
  }

  const doneCount = Array.from(state.taskState.values()).filter((s) => s === "done").length;
  const completionRate = state.summary.total_tasks > 0 ? (doneCount / state.summary.total_tasks) * 100 : 0;
  const completedEvents = state.events.filter((event) => numberValue(event.completion_time) <= state.currentTime + 1e-9);
  const activeEvents = state.events.filter(
    (event) =>
      numberValue(event.dispatch_time) <= state.currentTime + 1e-9 &&
      numberValue(event.completion_time) > state.currentTime + 1e-9
  );
  const multiTotal = state.events.filter((event) => (event.vehicle_ids || []).length > 1).length;
  const multiCompleted = completedEvents.filter((event) => (event.vehicle_ids || []).length > 1).length;
  const multiActive = activeEvents.filter((event) => (event.vehicle_ids || []).length > 1).length;
  const vehicles = Array.from(state.vehicleState.values());
  let activeVehicles = 0;
  vehicles.forEach((vehicle) => {
    if (state.currentTime < vehicle.busyUntil - 1e-9) {
      activeVehicles += 1;
    }
  });
  const utilization = vehicles.length > 0 ? (activeVehicles / vehicles.length) * 100 : 0;

  dom.summaryBox.textContent = `strategy=${state.summary.strategy} | scene=${state.summary.scenario} | progress=${state.eventIndex}/${state.events.length}`;

  const items = [
    `Current simulation time: ${state.currentTime.toFixed(1)}`,
    `Accumulated score: ${state.scoreAcc.toFixed(2)}`,
    `Task completion rate: ${completionRate.toFixed(1)}%`,
    `Vehicle utilization: ${utilization.toFixed(1)}%`,
    `Total tasks: ${state.summary.total_tasks}`,
    `Completed in replay: ${doneCount}`,
    `Multi-vehicle tasks (replay): ${multiCompleted}/${multiTotal} | active now ${multiActive}`,
    `Unserved: ${state.summary.unserved_tasks}`,
    `Overtime: ${state.summary.overtime_tasks}`,
    `Final score (simulation): ${Number(state.summary.final_score).toFixed(2)}`,
    `Total distance: ${Number(state.summary.total_distance).toFixed(2)}`,
    `Total charging wait: ${Number(state.summary.total_charging_wait).toFixed(2)}`,
    `Average response time: ${Number(state.summary.avg_response_time).toFixed(2)}`
  ];

  if (initial) {
    items.unshift(`Seed: ${dom.seedInput.value}`);
  }

  dom.metricsList.innerHTML = "";
  items.forEach((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    dom.metricsList.appendChild(li);
  });
}

function renderStatusPanels() {
  renderVehicleStatuses();
  renderTaskList();
}

function renderVehicleStatuses() {
  if (!state.scenario || !state.vehicleState.size) {
    dom.vehicleStatusList.className = "status-list empty";
    dom.vehicleStatusList.textContent = "No vehicle data";
    return;
  }

  dom.vehicleStatusList.className = "status-list";
  dom.vehicleStatusList.innerHTML = "";

  const vehicles = Array.from(state.vehicleState.values()).sort((a, b) => {
    const statusA = resolveVehicleStatus(a, state.currentTime);
    const statusB = resolveVehicleStatus(b, state.currentTime);
    const pri = { transporting: 0, charging: 1, idle: 2 };
    const pa = pri[statusA] ?? 3;
    const pb = pri[statusB] ?? 3;
    if (pa !== pb) {
      return pa - pb;
    }
    return a.vehicleId - b.vehicleId;
  });
  vehicles.forEach((vehicle) => {
    const status = resolveVehicleStatus(vehicle, state.currentTime);
    vehicle.status = status;

    const batteryPct = vehicle.batteryCapacity > 0 ? (vehicle.battery / vehicle.batteryCapacity) * 100 : 0;

    const card = document.createElement("div");
    card.className = "vehicle-item";

    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML = `<span>Vehicle #${vehicle.vehicleId}</span><span class=\"status-pill ${status}\">${status}</span>`;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = [
      `Battery: ${batteryPct.toFixed(1)}% (${vehicle.battery.toFixed(1)}/${vehicle.batteryCapacity.toFixed(1)})`,
      `Load: ${vehicle.assignedWeight.toFixed(1)}/${vehicle.capacity.toFixed(1)}`,
      `Last task: ${vehicle.lastTaskId === null ? "-" : `#${vehicle.lastTaskId}`}`,
      `Node: ${vehicle.currentNode}`
    ].join("<br>");

    card.appendChild(head);
    card.appendChild(meta);
    dom.vehicleStatusList.appendChild(card);
  });
}

function resolveVehicleStatus(vehicle, now) {
  const vehicleId = Number(vehicle.vehicleId);
  const busyUntil = Number(vehicle.busyUntil ?? 0);
  const isAnimating = state.activeReplayVehicleIds.has(vehicleId);
  const isBusyBySchedule = Number.isFinite(busyUntil) && now < busyUntil - 1e-9;
  if (!isAnimating && !isBusyBySchedule) {
    return "idle";
  }
  if (
    vehicle.chargeStartTime !== null &&
    vehicle.chargeEndTime !== null &&
    now >= vehicle.chargeStartTime - 1e-9 &&
    now < vehicle.chargeEndTime - 1e-9
  ) {
    return "charging";
  }
  return "transporting";
}

function renderTaskList() {
  if (!state.scenario) {
    dom.taskListBox.className = "status-list empty";
    dom.taskListBox.textContent = "No task data";
    return;
  }

  dom.taskListBox.className = "status-list";
  dom.taskListBox.innerHTML = "";

  const tasks = [...state.scenario.tasks].sort((a, b) => {
    const ra = state.recentTaskIds.has(a.task_id) ? 0 : 1;
    const rb = state.recentTaskIds.has(b.task_id) ? 0 : 1;
    if (ra !== rb) {
      return ra - rb;
    }
    const sa = taskStatusOrder(state.taskState.get(a.task_id) || "pending");
    const sb = taskStatusOrder(state.taskState.get(b.task_id) || "pending");
    if (sa !== sb) {
      return sa - sb;
    }
    return a.release_time - b.release_time || a.task_id - b.task_id;
  });
  const limit = tasks.length;

  for (let i = 0; i < limit; i += 1) {
    const task = tasks[i];
    const status = state.taskState.get(task.task_id) || "pending";
    const card = document.createElement("div");
    card.className = `task-item ${status}`;

    const isRecent = state.recentTaskIds.has(task.task_id);
    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML = `<span>Task #${task.task_id}</span><span>${isRecent ? `updated (${status})` : status}</span>`;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = [
      `Node: ${task.node_id}`,
      `Weight: ${Number(task.weight || 0).toFixed(1)}`,
      `Release: ${task.release_time}`,
      `Deadline: ${task.deadline}`
    ].join("<br>");

    card.appendChild(head);
    card.appendChild(meta);
    dom.taskListBox.appendChild(card);
  }
}

function taskStatusOrder(status) {
  if (status === "delivering") {
    return 0;
  }
  if (status === "done") {
    return 1;
  }
  if (status === "pending") {
    return 2;
  }
  return 3;
}

function finalizeReplayTaskStates() {
  state.scenario.tasks.forEach((task) => {
    const status = state.taskState.get(task.task_id) || "pending";
    if (status === "done") {
      return;
    }
    if (state.completedTaskIds.has(task.task_id)) {
      state.taskState.set(task.task_id, "done");
      return;
    }
    state.taskState.set(task.task_id, "unserved");
  });
}

function appendLog(event) {
  const line = `Task#${event.task_id} | vehicle=${(event.vehicle_ids || []).join(",")} | dispatch=${Number(event.dispatch_time).toFixed(1)} | complete=${Number(event.completion_time).toFixed(1)} | score=${Number(event.score).toFixed(2)} | dist=${Number(event.total_distance).toFixed(2)}`;
  dom.logBox.textContent += `${line}\n`;
  dom.logBox.scrollTop = dom.logBox.scrollHeight;
}

function setStatus(message, isError = false) {
  dom.statusBar.textContent = message;
  dom.statusBar.style.color = isError ? "#A22A39" : "#3F556A";
}

function vehicleColor(vehicleId) {
  return VEHICLE_COLORS[vehicleId % VEHICLE_COLORS.length];
}

function buildProjection(nodes) {
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);

  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const spanX = Math.max(1e-6, maxX - minX);
  const spanY = Math.max(1e-6, maxY - minY);

  const scaleX = (MAP_WIDTH - 2 * MAP_PADDING) / spanX;
  const scaleY = (MAP_HEIGHT - 2 * MAP_PADDING) / spanY;
  const scale = Math.min(scaleX, scaleY);

  const usedW = spanX * scale;
  const usedH = spanY * scale;
  const offsetX = (MAP_WIDTH - usedW) / 2;
  const offsetY = (MAP_HEIGHT - usedH) / 2;

  return { minX, minY, scale, offsetX, offsetY };
}

function projectNode(nodeId) {
  const node = state.nodeMap.get(nodeId);
  if (!node || !state.projection) {
    return null;
  }
  const x = state.projection.offsetX + (node.x - state.projection.minX) * state.projection.scale;
  const y = MAP_HEIGHT - (state.projection.offsetY + (node.y - state.projection.minY) * state.projection.scale);
  return { x, y };
}

function routeToPoints(routeNodes) {
  if (!Array.isArray(routeNodes)) {
    return [];
  }
  const points = [];
  routeNodes.forEach((nodeId) => {
    const point = projectNode(nodeId);
    if (!point) {
      return;
    }
    const prev = points[points.length - 1];
    if (!prev || prev.x !== point.x || prev.y !== point.y) {
      points.push(point);
    }
  });
  return points;
}

function formatPoints(points) {
  return points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
}

function pathMetrics(points) {
  const cumulative = [0];
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i].x - points[i - 1].x;
    const dy = points[i].y - points[i - 1].y;
    cumulative.push(cumulative[i - 1] + Math.hypot(dx, dy));
  }
  return {
    cumulative,
    total: cumulative[cumulative.length - 1]
  };
}

function sampleAtDistance(points, cumulative, dist) {
  if (!points.length) {
    return { x: 0, y: 0 };
  }
  if (dist <= 0) {
    return points[0];
  }

  const total = cumulative[cumulative.length - 1];
  if (dist >= total) {
    return points[points.length - 1];
  }

  for (let i = 1; i < cumulative.length; i += 1) {
    if (dist <= cumulative[i]) {
      const segStart = cumulative[i - 1];
      const segEnd = cumulative[i];
      const ratio = (dist - segStart) / Math.max(1e-6, segEnd - segStart);
      return {
        x: points[i - 1].x + (points[i].x - points[i - 1].x) * ratio,
        y: points[i - 1].y + (points[i].y - points[i - 1].y) * ratio
      };
    }
  }

  return points[points.length - 1];
}

function svg(tag, attrs = {}, text = "") {
  const el = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => {
    el.setAttribute(key, String(value));
  });
  if (text) {
    el.textContent = text;
  }
  return el;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return response.json();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
