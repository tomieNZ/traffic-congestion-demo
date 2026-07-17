/*
 * The Dashboard intentionally keeps its client code small. It does not know
 * the HMAC secret: the API supplies pre-signed demo payloads, and this page
 * submits them exactly as a sensor client would. That makes the tampered case
 * a genuine signature failure instead of a front-end-only simulation.
 */

const TOKEN_STORAGE_KEY = "traffic-demo.access-token";
const USER_STORAGE_KEY = "traffic-demo.operator";

/*
 * localStorage is used here because this is a classroom demonstration and the
 * requested behaviour is to survive a page refresh. A production application
 * should use a Secure, HttpOnly, SameSite cookie instead of exposing a JWT to
 * JavaScript storage.
 */
function readStoredSession() {
  try {
    return {
      token: window.localStorage.getItem(TOKEN_STORAGE_KEY),
      username: window.localStorage.getItem(USER_STORAGE_KEY) || "operator",
    };
  } catch (_error) {
    return { token: null, username: "operator" };
  }
}

function storeSession(username, token) {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    window.localStorage.setItem(USER_STORAGE_KEY, username);
  } catch (_error) {
    /* The in-memory token still allows the current page to operate. */
  }
}

function clearStoredSession() {
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(USER_STORAGE_KEY);
  } catch (_error) {
    /* Storage may be unavailable in a restricted browser context. */
  }
}

const storedSession = readStoredSession();
let accessToken = storedSession.token;
let operatorName = storedSession.username;
let scenarios = {};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  authStatus: document.querySelector("#auth-status"),
  sessionBanner: document.querySelector("#session-banner"),
  result: document.querySelector("#result"),
  latestDecision: document.querySelector("#latest-decision"),
  auditList: document.querySelector("#audit-list"),
  loginForm: document.querySelector("#login-form"),
  loginSubmit: document.querySelector("#login-submit"),
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

function setSessionBanner(state, title, message) {
  elements.sessionBanner.dataset.state = state;
  elements.sessionBanner.querySelector("strong").textContent = title;
  elements.sessionBanner.querySelector("span").textContent = message;
}

function renderSignedOutState(
  title = "Sign in required",
  message = "Authenticate as the demo operator to unlock the signed sensor scenarios."
) {
  elements.authStatus.textContent = "Not authenticated";
  setSessionBanner("signed-out", title, message);
  document.body.classList.remove("dashboard-ready");
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.disabled = true;
    button.classList.remove("is-ready", "is-selected");
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-pressed", "false");
  });
}

function renderDashboardReady() {
  elements.authStatus.textContent = `Authenticated as ${operatorName}`;
  setSessionBanner(
    "ready",
    "Dashboard ready",
    "Signed in successfully. Choose a scenario to send a signed sensor reading."
  );
  document.body.classList.add("dashboard-ready");
  elements.result.innerHTML = `
    <div class="result-success">
      <strong>Login successful</strong>
      <span>Protected scenario controls are ready.</span>
    </div>`;
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

function renderRequestError(error) {
  elements.result.innerHTML = `
    <div class="result-error">
      <strong>Request failed</strong>
      <span>${error.message || "The demo could not complete the request."}</span>
    </div>`;
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

function handleProtectedFailure(response, fallbackMessage) {
  if (response.status === 401) {
    accessToken = null;
    clearStoredSession();
    renderSignedOutState(
      "Session expired",
      "Sign in again to continue using the protected demo controls."
    );
  }
  throw new Error(response.payload.message || fallbackMessage);
}

async function fetchScenarios() {
  const response = await requestJson("/api/v1/demo/scenarios");
  if (!response.ok) {
    handleProtectedFailure(response, "Unable to load signed demo scenarios.");
  }
  if (!response.payload.scenarios) {
    throw new Error("The server returned no demo scenarios.");
  }
  scenarios = response.payload.scenarios;
  return scenarios;
}

async function refreshHistory() {
  const response = await requestJson("/api/v1/traffic/history?limit=20");
  if (!response.ok) {
    handleProtectedFailure(response, "Unable to load the audit history.");
  }
  renderHistory(response.payload.events || []);
}

function setScenarioButtonsBusy(isBusy) {
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.disabled = isBusy || !accessToken || !scenarios[button.dataset.scenario];
  });
}

function enableScenarioButtons() {
  /* Bind each button once so a session restore or re-login cannot duplicate handlers. */
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    const hasScenario = Boolean(scenarios[button.dataset.scenario]);
    button.disabled = !hasScenario || !accessToken;
    button.classList.toggle("is-ready", hasScenario && Boolean(accessToken));
    button.setAttribute("aria-disabled", String(button.disabled));
    button.setAttribute("aria-pressed", "false");
    if (button.dataset.bound !== "true") {
      button.addEventListener("click", () => runScenario(button.dataset.scenario));
      button.dataset.bound = "true";
    }
  });
}

async function runScenario(name) {
  if (!accessToken) {
    renderSignedOutState();
    return;
  }

  const selectedButton = document.querySelector(`[data-scenario="${name}"]`);
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    const isSelected = button === selectedButton;
    button.classList.toggle("is-selected", isSelected);
    button.setAttribute("aria-pressed", String(isSelected));
  });
  elements.result.innerHTML = `<span class="result-placeholder">Preparing a fresh signed ${name} reading…</span>`;
  setScenarioButtonsBusy(true);

  try {
    /*
     * Fetch the payload immediately before evaluation. The server signs it
     * with its current HMAC secret, so the browser never invents or reuses a
     * signature that could be stale after a Render restart or redeploy.
     */
    const latestScenarios = await fetchScenarios();
    const scenario = latestScenarios[name];
    if (!scenario || !scenario.payload || !scenario.payload.signature) {
      throw new Error("The selected scenario did not include a server signature.");
    }

    elements.result.innerHTML = `<span class="result-placeholder">Sending signed ${scenario.label.toLowerCase()}…</span>`;
    const response = await requestJson("/api/v1/traffic/evaluate", {
      method: "POST",
      body: JSON.stringify(scenario.payload),
    });
    renderResult(response);
    await refreshHistory();
  } catch (error) {
    renderRequestError(error);
  } finally {
    enableScenarioButtons();
  }
}

async function login() {
  /* The form uses the Render environment credentials supplied by the operator. */
  const username = document.querySelector("#login-username").value.trim();
  const password = document.querySelector("#login-password").value;
  elements.loginSubmit.disabled = true;
  elements.loginSubmit.textContent = "Signing in…";

  try {
    const response = await requestJson("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      elements.authStatus.textContent = "Authentication failed";
      throw new Error(response.payload.message || "Unable to authenticate.");
    }
    if (!response.payload.access_token) {
      throw new Error("The login response did not include an access token.");
    }

    accessToken = response.payload.access_token;
    operatorName = username || "operator";
    storeSession(operatorName, accessToken);
    elements.authStatus.textContent = `Authenticated as ${operatorName}`;
    setSessionBanner(
      "ready",
      "Login successful",
      "Refreshing the dashboard and restoring the authenticated session…"
    );
  } finally {
    elements.loginSubmit.disabled = false;
    elements.loginSubmit.textContent = "Sign in";
  }
}

async function loadAuthenticatedDashboard() {
  setSessionBanner("loading", "Restoring session", "Checking the saved login and loading signed scenarios…");
  await fetchScenarios();
  enableScenarioButtons();
  await refreshHistory();
  renderDashboardReady();
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await login();
    /* A full reload proves that the stored JWT, not an in-memory variable, restores the session. */
    window.location.reload();
  } catch (error) {
    setSessionBanner("error", "Sign-in failed", error.message);
    elements.result.innerHTML = `<div class="result-error"><strong>Sign-in failed</strong><span>${error.message}</span></div>`;
  }
});

document.querySelector("#refresh-history").addEventListener("click", async () => {
  try {
    await refreshHistory();
    elements.result.innerHTML = `
      <div class="result-success">
        <strong>Audit log refreshed</strong>
        <span>The latest protected events are now displayed.</span>
      </div>`;
  } catch (error) {
    renderRequestError(error);
  }
});

async function initialise() {
  renderSignedOutState();
  const health = await requestJson("/health");
  if (health.ok) {
    renderHealth(health.payload);
  } else {
    elements.serviceStatus.textContent = "Service unavailable";
  }

  if (!accessToken) {
    elements.result.innerHTML = '<span class="result-placeholder">Sign in above to load the protected demo controls.</span>';
    return;
  }

  try {
    await loadAuthenticatedDashboard();
  } catch (error) {
    if (accessToken) {
      elements.serviceStatus.textContent = "Connection error";
      setSessionBanner("error", "Session could not be restored", error.message);
      elements.result.innerHTML = `<div class="result-error"><strong>Dashboard unavailable</strong><span>${error.message}</span></div>`;
    }
  }
}

initialise().catch((error) => {
  elements.serviceStatus.textContent = "Connection error";
  setSessionBanner("error", "Demo unavailable", error.message);
  elements.result.innerHTML = `<div class="result-error"><strong>Demo unavailable</strong><span>${error.message}</span></div>`;
});
