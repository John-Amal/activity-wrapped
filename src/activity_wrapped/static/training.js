// Training tab: report, charts and plan builder. Uses helpers from app.js
// ($, api, esc, showError) and Chart.js (vendored in static/vendor).

(() => {
  const C = { accent: "#4fd1c5", pink: "#f472b6", amber: "#fbbf24", blue: "#7aa2f7", muted: "#8592a3",
    grid: "#222b35", low: "#4fd1c5", moderate: "#fbbf24", high: "#f87171" };
  if (window.Chart) {
    Chart.defaults.color = C.muted;
    Chart.defaults.borderColor = C.grid;
    Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.maintainAspectRatio = false;
  }
  const charts = {};
  let lastGroup = null;

  const clock = (s) => {
    if (s == null || !isFinite(s)) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  const monthLabel = (m) => {
    const [y, mo] = m.split("-");
    return new Date(Number(y), Number(mo) - 1, 1).toLocaleString(undefined, { month: "short", year: "2-digit" });
  };
  const lowerBetter = (proxy) => proxy === "run" || proxy === "swim";
  const fmtProxy = (v, proxy) => (lowerBetter(proxy) ? clock(v) : Math.round(v * 10) / 10);

  function draw(id, config) {
    if (!window.Chart) return;
    charts[id]?.destroy();
    charts[id] = new Chart(document.getElementById(id), config);
  }

  // --- report -----------------------------------------------------------------
  async function loadTraining() {
    const group = $("t-group").value;
    $("p-target-wrap").classList.toggle("hidden", group !== "run");
    $("t-status").textContent = "Analysing your training…";
    try {
      const r = await api(`/api/training/report?group=${group}`);
      if (r.empty) {
        $("t-status").textContent = `Not enough ${r.label.toLowerCase()} yet: at least 5 activities are needed.`;
        ["t-kpis", "t-findings"].forEach((id) => { $(id).innerHTML = ""; });
        return;
      }
      $("t-status").textContent = `${r.count} activities analysed.`;
      renderKpis(r, group);
      renderFindings(r.findings);
      renderPredictions(r.predictions);
      renderZones(r.zones);
      renderCharts(r, group);
      if (lastGroup !== group) $("t-plan").classList.add("hidden");
      lastGroup = group;
    } catch (err) {
      $("t-status").textContent = "";
      showError(err.message);
    }
  }

  function kpi(label, value, hint = "") {
    return `<div class="kpi"><span class="kpi-label">${esc(label)}</span><span class="kpi-value">${esc(value)}</span>${hint ? `<span class="kpi-hint">${esc(hint)}</span>` : ""}</div>`;
  }

  function renderKpis(r, group) {
    const s = r.summary, th = r.thresholds;
    const items = [
      group === "run" || group === "swim" ? kpi("Weekly distance", `${s.weekly_km} km`, "last 4 weeks") : kpi("Weekly time", `${s.weekly_hours} h`, "last 4 weeks"),
      kpi("Fitness", s.fitness, "42-day load"),
      kpi("Fatigue", s.fatigue, "7-day load"),
      kpi("Form", (s.form > 0 ? "+" : "") + s.form, "fitness − fatigue"),
      kpi("Load ratio", s.acwr ?? "–", "last week vs usual"),
    ];
    if (group === "run" && th.critical_speed) items.push(kpi("Critical speed", th.critical_speed.pace, `fitted on ${th.critical_speed.points} efforts`));
    if (group === "ride" && th.ftp) items.push(kpi("Threshold power", `${th.ftp} W`, "estimate"));
    if (r.performance?.current_display) items.push(kpi(r.performance.label, r.performance.current_display, "best, last 90 days"));
    if (th.hr_max) items.push(kpi("Max heart rate", `${th.hr_max} bpm`, "from your data"));
    if (r.cadence) items.push(kpi("Cadence", `${r.cadence} spm`, "median, 90 days"));
    $("t-kpis").innerHTML = items.join("");
  }

  function renderFindings(findings) {
    $("t-findings").innerHTML = findings.map((f) => `
      <div class="finding ${f.status}">
        <div class="f-head"><span class="f-dot"></span><strong>${esc(f.title)}</strong></div>
        <p>${esc(f.detail)}</p>
        ${f.advice ? `<p class="f-advice">${esc(f.advice)}</p>` : ""}
      </div>`).join("");
  }

  function renderPredictions(preds) {
    $("t-predictions-wrap").classList.toggle("hidden", !preds?.length);
    if (!preds?.length) return;
    $("t-predictions").innerHTML = preds.map((p) => `
      <tr>
        <td>${esc(p.name)}</td>
        <td class="pb">${esc(p.time)}</td>
        <td>${esc(p.range[0])}–${esc(p.range[1])}</td>
        <td>${esc(p.pace)}</td>
        <td>${p.pb ? esc(p.pb) : "<span class='muted'>–</span>"}${p.pb_within_reach ? " <span class='badge'>PR in reach</span>" : ""}${p.note ? `<div class="muted small">${esc(p.note)}</div>` : ""}</td>
      </tr>`).join("");
  }

  function renderZones(zones) {
    $("t-zones-wrap").classList.toggle("hidden", !zones);
    if (!zones) return;
    $("t-zones").innerHTML = zones.map((z) => `<div class="zone"><span>${esc(z.zone)}</span><strong>${esc(z.from)}–${esc(z.to)} /km</strong></div>`).join("");
  }

  function renderCharts(r, group) {
    const distanceUnit = group === "run" || group === "swim";
    draw("c-weekly", {
      data: {
        labels: r.weekly.map((w) => w.week.slice(5)),
        datasets: [
          { type: "bar", label: distanceUnit ? "km" : "hours", data: r.weekly.map((w) => distanceUnit ? w.distance_km : w.hours), backgroundColor: C.accent + "aa", yAxisID: "y" },
          { type: "line", label: "load", data: r.weekly.map((w) => w.load), borderColor: C.amber, pointRadius: 0, tension: 0.3, yAxisID: "y2" },
        ],
      },
      options: { scales: { y: { beginAtZero: true }, y2: { position: "right", beginAtZero: true, grid: { display: false } } } },
    });

    const f = r.fitness;
    draw("c-fitness", {
      type: "line",
      data: {
        labels: f.dates.map((d) => d.slice(5)),
        datasets: [
          { label: "Fitness", data: f.fitness, borderColor: C.accent, pointRadius: 0, borderWidth: 2 },
          { label: "Fatigue", data: f.fatigue, borderColor: C.pink, pointRadius: 0, borderWidth: 1.5 },
          { label: "Form", data: f.form, borderColor: C.amber, pointRadius: 0, borderWidth: 1.5, borderDash: [5, 4] },
        ],
      },
      options: { scales: { x: { ticks: { maxTicksLimit: 8 } } } },
    });

    const perf = r.performance;
    $("f-performance").classList.toggle("hidden", !perf);
    if (perf) {
      $("c-performance-title").textContent = `${perf.label} by month (${perf.direction})`;
      draw("c-performance", {
        type: "line",
        data: {
          labels: perf.series.map((s) => monthLabel(s.month)),
          datasets: [{ label: perf.label, data: perf.series.map((s) => s.value), borderColor: C.accent, backgroundColor: C.accent, tension: 0.25 }],
        },
        options: {
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => perf.series[c.dataIndex].display } } },
          scales: { y: { reverse: lowerBetter(perf.proxy), ticks: { callback: (v) => fmtProxy(v, perf.proxy) } } },
        },
      });
    }

    draw("c-intensity", {
      type: "bar",
      data: {
        labels: r.intensity.map((m) => monthLabel(m.month)),
        datasets: [
          { label: "Easy", data: r.intensity.map((m) => m.low), backgroundColor: C.low },
          { label: "Moderate", data: r.intensity.map((m) => m.moderate), backgroundColor: C.moderate },
          { label: "Hard", data: r.intensity.map((m) => m.high), backgroundColor: C.high },
        ],
      },
      options: { scales: { x: { stacked: true }, y: { stacked: true } } },
    });

    const eff = r.efficiency || [];
    $("f-efficiency").classList.toggle("hidden", eff.length < 3);
    if (eff.length >= 3) {
      draw("c-efficiency", {
        type: "line",
        data: { labels: eff.map((e) => monthLabel(e.month)), datasets: [{ label: "m/min per bpm", data: eff.map((e) => e.value), borderColor: C.blue, backgroundColor: C.blue, tension: 0.25 }] },
        options: { plugins: { legend: { display: false } } },
      });
    }
  }

  // --- plan -----------------------------------------------------------------------
  function planParams() {
    const p = new URLSearchParams({
      group: $("t-group").value, goal: $("p-goal").value, target: $("p-target").value,
      weeks: $("p-weeks").value, days: $("p-days").value,
    });
    if ($("p-race").value) p.set("race_date", $("p-race").value);
    return p;
  }

  async function buildPlan() {
    const btn = $("p-build");
    btn.disabled = true;
    try {
      const plan = await api(`/api/training/plan?${planParams()}`);
      renderPlan(plan);
      $("p-ics").href = `/api/training/plan.ics?${planParams()}`;
      $("t-plan").classList.remove("hidden");
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  function scenarioCard(title, sc, race) {
    if (!sc) return "";
    const main = race ? sc.race_time : sc.display;
    const range = race ? sc.race_range : sc.range;
    return `<div class="kpi"><span class="kpi-label">${esc(title)}</span><span class="kpi-value">${esc(main)}</span><span class="kpi-hint">likely ${esc(range[0])}–${esc(range[1])}</span></div>`;
  }

  function renderPlan(plan) {
    const pr = plan.projection || {};
    const race = pr.race_time !== undefined || pr.current_race_time !== undefined;
    let html = "";
    if (pr.current !== undefined) {
      const raceLabel = race ? ` (${pr.race})` : "";
      html += `<div class="kpis">
        <div class="kpi"><span class="kpi-label">Now${esc(raceLabel)}</span><span class="kpi-value">${esc(race ? pr.current_race_time : pr.current_display)}</span><span class="kpi-hint">best, last 90 days</span></div>
        ${pr.available ? scenarioCard("Following this plan", pr.plan, race) : ""}
        ${pr.available ? scenarioCard("Keeping your current load", pr.keep, race) : ""}
        ${scenarioCard("If your 12-month trend continues", pr.trend, race)}
      </div>`;
      if (!pr.available && pr.reason) html += `<p class="hint">No load-based projection: ${esc(pr.reason)}.</p>`;
      if (pr.available && pr.clamped) html += `<p class="hint">The plan takes your fitness beyond anything in your history, so its projection is capped at just above your past range.</p>`;
      if (pr.note) html += `<p class="hint">${esc(pr.note)}</p>`;
    } else if (pr.reason) {
      html += `<p class="hint">No projection: ${esc(pr.reason)}.</p>`;
    }
    $("p-projection").innerHTML = html;
    renderProjectionChart(pr);

    $("p-notes").innerHTML = (plan.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    $("p-weeks-list").innerHTML = plan.weeks.map((w, i) => `
      <details class="week" ${i === 0 ? "open" : ""}>
        <summary><strong>Week ${w.index}</strong> · ${esc(w.start)} · <span class="kind ${w.kind.replace(" ", "-")}">${esc(w.kind)}</span><span class="muted"> · ${w.volume} ${esc(w.unit)}</span></summary>
        <table class="bests sessions">
          <thead><tr><th>Day</th><th>Session</th><th>Amount</th><th>Target</th><th>Time</th></tr></thead>
          <tbody>${w.sessions.map((s) => `
            <tr class="s-${s.kind}">
              <td>${esc(s.day)}</td>
              <td><strong>${esc(s.title)}</strong><div class="muted small">${esc(s.description)}</div></td>
              <td>${s.amount} ${esc(s.unit)}</td>
              <td>${esc(s.target)}</td>
              <td>${s.minutes ? `~${s.minutes} min` : ""}</td>
            </tr>`).join("")}</tbody>
        </table>
      </details>`).join("");
  }

  function renderProjectionChart(pr) {
    const show = pr.history?.length && pr.current !== undefined;
    $("f-projection").classList.toggle("hidden", !show);
    if (!show) return;
    $("c-projection-title").textContent = `${pr.label}: monthly best, and where you could be by ${pr.end}`;
    // History as a line, then one slot per scenario, each with its likely range.
    const scenarios = [];
    if (pr.available) scenarios.push(["Plan", pr.plan, C.amber], ["Current load", pr.keep, C.blue]);
    if (pr.trend) scenarios.push(["Trend", pr.trend, C.pink]);
    const hist = pr.history.map((h) => h.value);
    const labels = [...pr.history.map((h) => monthLabel(h.month)), ...scenarios.map((s) => s[0])];
    const pad = (arr) => [...arr, ...scenarios.map(() => null)];
    const slot = (k, v) => labels.map((_, i) => (i === hist.length + k ? v : null));
    const datasets = [{ type: "line", label: "Monthly best", data: pad(hist), borderColor: C.accent, backgroundColor: C.accent, tension: 0.25 }];
    scenarios.forEach(([name, sc, colour], k) => {
      datasets.push({ type: "bar", label: `${name} (range)`, data: slot(k, [sc.low, sc.high]), backgroundColor: colour + "55", borderColor: colour, borderWidth: 1, barPercentage: 0.35, categoryPercentage: 1, grouped: false });
      datasets.push({ type: "line", label: name, data: slot(k, sc.value), borderColor: colour, backgroundColor: colour, pointRadius: 6, showLine: false });
    });
    const values = [...hist, ...scenarios.flatMap(([, sc]) => [sc.low, sc.high])];
    const lo = Math.min(...values), hi = Math.max(...values), margin = (hi - lo) * 0.1 || 1;
    draw("c-projection", {
      data: { labels, datasets },
      options: {
        plugins: { legend: { labels: { filter: (item) => !item.text.includes("(range)") } } },
        scales: { y: { min: lo - margin, max: hi + margin, reverse: lowerBetter(pr.proxy), ticks: { callback: (v) => fmtProxy(v, pr.proxy) } } },
      },
    });
  }

  $("t-group").onchange = loadTraining;
  $("p-build").onclick = buildPlan;
  $("p-goal").onchange = () => { $("p-target-wrap").classList.toggle("hidden", $("t-group").value !== "run"); };
  window.loadTraining = loadTraining;
})();
