// Activity Wrapped frontend. All state lives in `state`; every change
// re-requests the card PNG from the server, which is the single source of
// truth for what gets drawn (preview and download are the same image).

const THEME_SWATCH = { midnight: "#4fd1c5", paper: "#c2410c", forest: "#a3e635", dusk: "#f472b6" };

const state = {
  period: "last12",
  sports: new Set(),
  units: "metric",
  theme: "midnight",
  sections: new Set(),
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function params(extra = {}) {
  return new URLSearchParams({
    period: state.period,
    sports: [...state.sports].join(","),
    units: state.units,
    theme: state.theme,
    sections: [...state.sections].join(","),
    ...extra,
  });
}

let renderTimer = null;
function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(renderCard, 200);
}

function renderCard() {
  const img = $("card");
  img.classList.add("busy");
  img.onload = () => img.classList.remove("busy");
  img.onerror = () => showError("Could not render the card.");
  img.src = `/api/card.png?${params({ t: Date.now() })}`;
  $("download").href = `/api/card.png?${params({ download: "true" })}`;
}

function showError(msg) {
  $("error").textContent = msg;
  $("error").classList.toggle("hidden", !msg);
}

function buildPeriod(years) {
  const sel = $("period");
  const opts = [["last12", "Last 12 months"], ...years.map((y) => [`year:${y}`, String(y)]), ["all", "All time"]];
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  sel.value = state.period;
  sel.onchange = () => { state.period = sel.value; resetScanStatus(); scheduleRender(); };
}

function buildSports(sports) {
  const box = $("sports");
  const chip = (value, label) => `<button class="chip" data-value="${value}">${label}</button>`;
  box.innerHTML = chip("", "All") + sports.map((s) => chip(s.sport, `${s.label} <span>${s.count}</span>`)).join("");
  const sync = () => box.querySelectorAll(".chip").forEach((c) => {
    const v = c.dataset.value;
    c.classList.toggle("on", v === "" ? state.sports.size === 0 : state.sports.has(v));
  });
  box.onclick = (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    const v = c.dataset.value;
    if (v === "") state.sports.clear();
    else state.sports.has(v) ? state.sports.delete(v) : state.sports.add(v);
    sync(); resetScanStatus(); scheduleRender();
  };
  sync();
}

function buildUnits() {
  const seg = $("units");
  seg.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.units = b.dataset.value;
    seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    scheduleRender();
  };
}

function buildThemes(themes) {
  const box = $("themes");
  box.innerHTML = themes.map((t) =>
    `<button class="swatch" title="${t}" data-value="${t}" style="--c:${THEME_SWATCH[t] || "#888"}"></button>`).join("");
  const sync = () => box.querySelectorAll(".swatch").forEach((s) => s.classList.toggle("on", s.dataset.value === state.theme));
  box.onclick = (e) => {
    const s = e.target.closest(".swatch");
    if (!s) return;
    state.theme = s.dataset.value; sync(); scheduleRender();
  };
  sync();
}

function buildSections(sections) {
  if (state.sections.size === 0) sections.filter((s) => s.default).forEach((s) => state.sections.add(s.key));
  $("sections").innerHTML = sections.map((s) => `
    <label><input type="checkbox" value="${s.key}" ${state.sections.has(s.key) ? "checked" : ""}/> ${s.label}</label>`).join("");
  $("sections").onchange = (e) => {
    const cb = e.target;
    cb.checked ? state.sections.add(cb.value) : state.sections.delete(cb.value);
    scheduleRender();
  };
}

function resetScanStatus() { $("scan-status").textContent = ""; }

async function scan() {
  const btn = $("scan");
  btn.disabled = true;
  $("scan-status").textContent = "Scanning…";
  try {
    const r = await api(`/api/best-efforts/scan?${params()}`, { method: "POST" });
    let msg = r.total === 0 ? "No runs in this selection." : `${r.scanned} of ${r.total} runs scanned.`;
    if (!r.complete && r.stopped) msg += ` Stopped: ${r.stopped}. Press again to continue.`;
    $("scan-status").textContent = msg;
    state.sections.add("best_efforts");
    document.querySelector('#sections input[value="best_efforts"]').checked = true;
    renderCard();
  } catch (err) {
    $("scan-status").textContent = `Scan failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function loadWorkspace() {
  $("workspace").classList.remove("hidden");
  $("loading").classList.remove("hidden");
  try {
    const meta = await api("/api/meta");
    $("who").textContent = meta.demo ? "Demo athlete" : meta.athlete;
    buildPeriod(meta.years);
    buildSports(meta.sports);
    buildUnits();
    buildThemes(meta.themes);
    buildSections(meta.sections);
    renderCard();
  } catch (err) {
    showError(`Couldn't load your activities: ${err.message}`);
  } finally {
    $("loading").classList.add("hidden");
  }
}

async function init() {
  const me = await api("/api/me");
  const note = new URLSearchParams(location.search).get("note");
  if (note === "public_only") showError("Private activities were not shared, so only public ones are included.");
  if (!me.connected) {
    $("landing").classList.remove("hidden");
    if (!me.strava_configured) {
      $("connect").classList.add("hidden");
      $("not-configured").classList.remove("hidden");
    }
    return;
  }
  loadWorkspace();
}

$("scan").onclick = scan;
$("refresh").onclick = async () => {
  $("loading").classList.remove("hidden");
  try { await api("/api/sync", { method: "POST" }); await loadWorkspace(); }
  catch (err) { showError(err.message); }
  finally { $("loading").classList.add("hidden"); }
};
$("logout").onclick = async () => { await api("/auth/logout", { method: "POST" }); location.href = "/"; };

init().catch((err) => showError(err.message));
