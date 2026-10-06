// Activity Wrapped frontend. All state lives in `state`; images are always
// rendered by the server, so what you preview is exactly what you download.

const THEME_SWATCH = { midnight: "#4fd1c5", paper: "#c2410c", forest: "#a3e635", dusk: "#f472b6" };
const PAGE = 50;

const state = {
  tab: "card",
  period: "last12",
  sports: new Set(),
  units: "metric",
  theme: "midnight",
  privacy: 200,
  map: "none",
  bg: "solid",
  sections: new Set(),
  search: "",
  sort: "date",
  offset: 0,
  activityId: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

const filters = (extra = {}) => new URLSearchParams({
  period: state.period, sports: [...state.sports].join(","), units: state.units, ...extra,
});
const look = (extra = {}) => filters({ theme: state.theme, privacy: state.privacy, map: state.map, bg: state.bg, ...extra });

function showError(msg) {
  $("error").textContent = msg || "";
  $("error").classList.toggle("hidden", !msg);
}

// --- refresh whatever the current tab shows, debounced ----------------------
let timer = null;
function refresh() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    if (state.tab === "card") renderCard();
    if (state.tab === "activities") state.activityId ? openActivity(state.activityId) : loadActivities(true);
    if (state.tab === "bests") loadBests();
    if (state.tab === "training" && window.loadTraining) window.loadTraining();
  }, 200);
}

function setImage(img, url) {
  img.classList.toggle("clear", state.bg === "transparent");
  img.classList.add("busy");
  img.onload = () => img.classList.remove("busy");
  img.onerror = () => showError("Could not render the image.");
  img.src = url;
}

// --- wrapped card -------------------------------------------------------------
function renderCard() {
  const p = look({ sections: [...state.sections].join(",") });
  setImage($("card"), `/api/card.png?${p}&t=${Date.now()}`);
  $("download").href = `/api/card.png?${p}&download=true`;
}

// --- activities ---------------------------------------------------------------
async function loadActivities(reset) {
  if (reset) state.offset = 0;
  $("activity-list-view").classList.remove("hidden");
  $("activity-detail-view").classList.add("hidden");
  try {
    const r = await api(`/api/activities?${filters({ q: state.search, sort: state.sort, limit: PAGE, offset: state.offset })}`);
    const rows = r.items.map((a) => `
      <li data-id="${a.id}">
        <div class="a-main">
          <span class="a-name">${esc(a.name)}</span>
          <span class="a-sub">${esc(a.sport_label)} · ${esc(a.date)}${a.achievements ? ` · ${a.achievements} achievement${a.achievements > 1 ? "s" : ""}` : ""}</span>
        </div>
        <div class="a-nums">
          <span>${esc(a.distance)}</span><span>${esc(a.moving_time)}</span><span>${esc(a.pace || "")}</span>
        </div>
        <span class="a-map" title="${a.has_map ? "Has a route map" : "No GPS"}">${a.has_map ? "◎" : ""}</span>
      </li>`).join("");
    const list = $("activity-list");
    list.innerHTML = reset ? rows : list.innerHTML + rows;
    const shown = Math.min(state.offset + r.items.length, r.total);
    $("activity-count").textContent = r.total ? `${shown} of ${r.total} activities` : "No activities match these filters.";
    $("more").classList.toggle("hidden", shown >= r.total);
  } catch (err) {
    showError(err.message);
  }
}

async function openActivity(id) {
  state.activityId = id;
  $("activity-list-view").classList.add("hidden");
  $("activity-detail-view").classList.remove("hidden");
  const p = look({ t: Date.now() });
  setImage($("activity-card"), `/api/activity/${id}/card.png?${p}`);
  $("activity-download").href = `/api/activity/${id}/card.png?${look({ download: "true" })}`;
  try {
    const d = await api(`/api/activity/${id}?units=${state.units}`);
    $("activity-warning").textContent = d.warning || "";
    $("activity-warning").classList.toggle("hidden", !d.warning);
  } catch (_) { /* the image request reports errors */ }
}

// --- personal bests -------------------------------------------------------------
async function loadBests() {
  try {
    const r = await api(`/api/personal-bests?${filters()}`);
    $("bests-status").textContent = r.total === 0
      ? "No runs in this selection."
      : r.scanned === 0
        ? `None of the ${r.total} runs in this selection have been scanned yet. Use "Scan runs for PRs".`
        : `From ${r.scanned} of ${r.total} runs scanned${r.scanned < r.total ? " — scan more to complete the list" : ""}.`;
    $("bests-body").innerHTML = r.bests.map((b) => `
      <tr>
        <td>${esc(b.name)}</td>
        <td class="pb">${esc(b.time)}</td>
        <td>${esc(b.date)}</td>
        <td><a href="#" data-open="${b.activity_id}">${esc(b.activity)}</a></td>
        <td class="muted">${b.improvements ? `${b.improvements}× faster since ${esc(b.first_time)}` : "first effort"}</td>
      </tr>`).join("");
  } catch (err) {
    showError(err.message);
  }
}

// --- controls ---------------------------------------------------------------------
function buildPeriod(years) {
  const sel = $("period");
  const opts = [["last12", "Last 12 months"], ...years.map((y) => [`year:${y}`, String(y)]), ["all", "All time"]];
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  sel.value = state.period;
  sel.onchange = () => { state.period = sel.value; state.activityId = null; $("scan-status").textContent = ""; refresh(); };
}

function buildSports(sports) {
  const box = $("sports");
  const chip = (value, label) => `<button class="chip" data-value="${esc(value)}">${label}</button>`;
  box.innerHTML = chip("", "All") + sports.map((s) => chip(s.sport, `${esc(s.label)} <span>${s.count}</span>`)).join("");
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
    state.activityId = null;
    sync(); $("scan-status").textContent = ""; refresh();
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
    refresh();
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
    state.theme = s.dataset.value; sync(); refresh();
  };
  sync();
}

function buildPrivacy(choices, dflt) {
  const sel = $("privacy");
  state.privacy = dflt;
  sel.innerHTML = choices.map((m) => `<option value="${m}">${m === 0 ? "Off (show full routes)" : `Hide ${m >= 1000 ? `${m / 1000} km` : `${m} m`} at start and end`}</option>`).join("");
  sel.value = String(dflt);
  sel.onchange = () => {
    state.privacy = Number(sel.value);
    $("map-hint").classList.toggle("hidden", state.map === "none" || state.privacy >= 500);
    refresh();
  };
}

function updateHints() {
  if (state.tab === "training") return;
  $("map-hint").classList.toggle("hidden", state.map === "none" || state.privacy >= 500);
  $("bg-hint").classList.toggle("hidden", state.bg !== "transparent");
}

function buildMap(styles) {
  const sel = $("map");
  sel.innerHTML = styles.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join("");
  sel.value = state.map;
  sel.onchange = () => {
    state.map = sel.value;
    $("map-hint").classList.toggle("hidden", state.map === "none" || state.privacy >= 500);
    refresh();
  };
}

function buildBackground() {
  const seg = $("bg");
  seg.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.bg = b.dataset.value;
    seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    $("bg-hint").classList.toggle("hidden", state.bg !== "transparent");
    refresh();
  };
}

function buildSections(sections) {
  if (state.sections.size === 0) sections.filter((s) => s.default).forEach((s) => state.sections.add(s.key));
  $("sections").innerHTML = sections.map((s) => `
    <label><input type="checkbox" value="${s.key}" ${state.sections.has(s.key) ? "checked" : ""}/> ${esc(s.label)}</label>`).join("");
  $("sections").onchange = (e) => {
    const cb = e.target;
    cb.checked ? state.sections.add(cb.value) : state.sections.delete(cb.value);
    refresh();
  };
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  ["card", "activities", "bests", "training"].forEach((t) => $(`tab-${t}`).classList.toggle("hidden", t !== tab));
  document.querySelectorAll("[data-tab-only]").forEach((g) =>
    g.classList.toggle("hidden", !g.dataset.tabOnly.split(" ").includes(tab)));
  document.querySelectorAll("[data-tab-hide]").forEach((g) => {
    if (g.dataset.tabHide.split(" ").includes(tab)) g.classList.add("hidden");
    else if (!g.id || !g.id.endsWith("-hint")) g.classList.remove("hidden");
  });
  updateHints();
  showError("");
  refresh();
}

async function scan() {
  const btn = $("scan");
  btn.disabled = true;
  $("scan-status").textContent = "Scanning…";
  try {
    const r = await api(`/api/best-efforts/scan?${filters()}`, { method: "POST" });
    let msg = r.total === 0 ? "No runs in this selection." : `${r.scanned} of ${r.total} runs scanned.`;
    if (!r.complete && r.stopped) msg += ` Stopped: ${r.stopped}. Press again to continue.`;
    $("scan-status").textContent = msg;
    state.sections.add("best_efforts");
    const cb = document.querySelector('#sections input[value="best_efforts"]');
    if (cb) cb.checked = true;
    refresh();
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
    buildPrivacy(meta.privacy_choices, meta.privacy_default);
    buildMap(meta.map_styles);
    buildBackground();
    buildSections(meta.sections);
    switchTab(state.tab);
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
  const loginError = {
    login_expired: "That login couldn't be completed (the server may have restarted). Please connect again.",
    access_denied: "Strava access wasn't granted, so nothing was loaded.",
  }[new URLSearchParams(location.search).get("error")];
  if (!me.connected) {
    $("landing").classList.remove("hidden");
    if (loginError) {
      $("landing-error").textContent = loginError;
      $("landing-error").classList.remove("hidden");
    }
    if (!me.strava_configured) {
      $("connect").classList.add("hidden");
      $("not-configured").classList.remove("hidden");
    }
    return;
  }
  loadWorkspace();
}

// --- wiring ------------------------------------------------------------------------
document.querySelector(".tabs").onclick = (e) => {
  const b = e.target.closest("button");
  if (b) switchTab(b.dataset.tab);
};
$("activity-list").onclick = (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) openActivity(Number(li.dataset.id));
};
$("bests-body").onclick = (e) => {
  const a = e.target.closest("a[data-open]");
  if (!a) return;
  e.preventDefault();
  state.activityId = Number(a.dataset.open);
  switchTab("activities");
};
$("back").onclick = () => { state.activityId = null; loadActivities(true); };
$("more").onclick = () => { state.offset += PAGE; loadActivities(false); };
let searchTimer = null;
$("search").oninput = (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.search = e.target.value; loadActivities(true); }, 250);
};
$("sort").onchange = (e) => { state.sort = e.target.value; loadActivities(true); };
$("scan").onclick = scan;
$("refresh").onclick = async () => {
  $("loading").classList.remove("hidden");
  try { await api("/api/sync", { method: "POST" }); await loadWorkspace(); }
  catch (err) { showError(err.message); }
  finally { $("loading").classList.add("hidden"); }
};
$("logout").onclick = async () => { await api("/auth/logout", { method: "POST" }); location.href = "/"; };

init().catch((err) => showError(err.message));
