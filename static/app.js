/*
 * The Dashboard intentionally keeps its client code small.  It does not know
 * the HMAC secret: the API supplies pre-signed demo payloads, and this page
 * submits them exactly as a sensor client would.  That makes the tampered case
 * a genuine signature failure instead of a front-end-only simulation.
 */

let accessToken = null;
let scenarios = {};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  authStatus: document.querySelector("#auth-status"),
  result: document.querySelector("#result"),
  latestDecision: document.querySelector("#latest-decision"),
  auditList: document.querySelector("#audit-list"),
};

async function requestJson(path, options = {}) {
  /* Centralize fetch behaviour so every protected request carries the JWT. */
  const headers = { ...(options.headers || {}) };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, { ...options, headers });
  const payload = await response.json();
  return { ok: response.ok, status: response.status, payload };
}

function setMetric(id, value) {
  document.querySelector(id).textContent = value;
}

function formatPercent(value) {
  return `${Math.round(Number(value) * 100)}%`;
}

function renderHealth(health) {
  elements.serviceStatus.textContent = "Service online";
  setMetric("#metric-accuracy", formatPercent(health.model_metrics.test_accuracy));
  setMetric("#metric-cv", formatPercent(health.model_metrics.five_fold_cv_accuracy));
  setMetric("#metric-records", health.model_metrics.training_records);
  setMetric("#metric-threshold", `dρ > ${health.congestion_threshold}`);
}

function renderLatest(result) {
  const status = result.status === "congested" ? "CONGESTED" : "SAFE";
  const rows = [
    ["Status", status],
    ["Road", result.road_id || "—"],
    ["Confidence", result.confidence ? formatPercent(result.confidence) : "—"],
    ["Recommended action", result.recommended_action || "—"],
  ];
  elements.latestDecision.innerHTML = rows
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
}

function renderResult(response) {
  const { ok, payload } = response;
  if (!ok) {
    elements.result.innerHTML = `
      <div class="result-error">
        <strong>${payload.error || "Request rejected"}</strong>
        <span>${payload.message || "The API rejected this reading."}</span>
      </div>`;
    return;
  }

  elements.result.innerHTML = `
    <div class="result-success">
      <strong>${payload.status.toUpperCase()} · ${payload.road_id}</strong>
      <span>SVM: ${payload.svm_status} · Rule: ${payload.rule_status} · Models agree: ${payload.models_agree}</span>
    </div>`;
  renderLatest(payload);
}

function renderHistory(events) {
  if (!events.length) {
    elements.auditList.innerHTML = '<p class="muted">No events yet.</p>';
    return;
  }
  elements.auditList.innerHTML = events
    .map((event) => {
      const time = new Date(event.created_at).toLocaleTimeString();
      const road = event.road_id || "—";
      return `
        <div class="audit-row">
          <time>${time}</time>
          <span>${event.event_type} · ${road}</span>
          <small class="${event.outcome}">${event.outcome}</small>
        </div>`;
    })
    .join("");
}

async function refreshHistory() {
  const response = await requestJson("/api/v1/traffic/history?limit=20");
  if (response.ok) {
    renderHistory(response.payload.events);
  }
}

async function runScenario(name) {
  const scenario = scenarios[name];
  if (!scenario) return;
  elements.result.innerHTML = '<span class="result-placeholder">Sending reading…</span>';
  const response = await requestJson("/api/v1/traffic/evaluate", {
    method: "POST",
    body: JSON.stringify(scenario.payload),
  });
  renderResult(response);
  await refreshHistory();
}

function enableScenarioButtons() {
  /* Mark buttons after binding so a manual re-login cannot duplicate handlers. */
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.disabled = false;
    if (button.dataset.bound !== "true") {
      button.addEventListener("click", () => runScenario(button.dataset.scenario));
      button.dataset.bound = "true";
    }
  });
}

async function login() {
  /* The defaults are convenient for class demonstration, but the form also
     permits operators to use credentials supplied through environment values. */
  const response = await requestJson("/auth/login", {
    method: "POST",
    body: JSON.stringify({
      username: document.querySelector("#login-username").value,
      password: document.querySelector("#login-password").value,
    }),
  });
  if (!response.ok) {
    elements.authStatus.textContent = "Authentication failed";
    throw new Error(response.payload.message || "Unable to authenticate.");
  }
  accessToken = response.payload.access_token;
  elements.authStatus.textContent = "Authenticated as operator";
}

async function initialise() {
  const health = await requestJson("/health");
  if (health.ok) renderHealth(health.payload);

  await login();
  const scenarioResponse = await requestJson("/api/v1/demo/scenarios");
  if (!scenarioResponse.ok) throw new Error("Unable to load demo scenarios.");
  scenarios = scenarioResponse.payload.scenarios;
  enableScenarioButtons();
  await refreshHistory();
}

document.querySelector("#refresh-history").addEventListener("click", refreshHistory);
document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await login();
    const scenarioResponse = await requestJson("/api/v1/demo/scenarios");
    scenarios = scenarioResponse.payload.scenarios;
    enableScenarioButtons();
    await refreshHistory();
  } catch (error) {
    elements.result.innerHTML = `<div class="result-error"><strong>Sign-in failed</strong><span>${error.message}</span></div>`;
  }
});
initialise().catch((error) => {
  elements.serviceStatus.textContent = "Connection error";
  elements.result.innerHTML = `<div class="result-error"><strong>Demo unavailable</strong><span>${error.message}</span></div>`;
});
