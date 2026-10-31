/* Dashboard analytics for the admin-authenticated /api/stats endpoint. */
const $ = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat();
const chartColors = ["#518bff", "#9370ff", "#36d7be", "#ffb654", "#ff6d86", "#62a7ff", "#65d6a8"];
let costChart;
let providerChart;
let tokenChart;

const fmtNum = (value) => numberFormat.format(Number(value) || 0);
const fmtUsd = (value) => {
  const amount = Number(value) || 0;
  return amount < 0.01
    ? `$${amount.toFixed(4)}`
    : `$${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);

function setNotice(message, type = "info") {
  const notice = $("notice");
  $("noticeText").textContent = message;
  notice.className = `notice notice-${type}`;
  notice.hidden = false;
}

function metricCard(label, value, detail, icon) {
  return `
    <article class="metric-card">
      <div class="metric-top">
        <span class="metric-label">${label}</span>
        <span class="metric-icon" aria-hidden="true">${icon}</span>
      </div>
      <div class="metric-value">${value}</div>
      <div class="metric-foot">${detail}</div>
    </article>`;
}

const metricIcons = {
  requests: '<svg viewBox="0 0 24 24"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4.5 7.8 7.5 4.4 7.5-4.4M12 12.2V21"/></svg>',
  tokens: '<svg viewBox="0 0 24 24"><path d="m12 3 9 4.5-9 4.5-9-4.5L12 3Z"/><path d="m3 12 9 4.5 9-4.5M3 16.5 12 21l9-4.5"/></svg>',
  cost: '<svg viewBox="0 0 24 24"><path d="M12 2v20m5-15.5C16 5.3 14.2 4.5 12 4.5c-2.5 0-4.5 1.2-4.5 3.2 0 5 9 2.8 9 7.8 0 2.1-2 4-5 4-2.3 0-4.4-.8-5.8-2.3"/></svg>',
  latency: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  success: '<svg viewBox="0 0 24 24"><path d="m5 12 4.5 4.5L19 7"/></svg>',
};

function renderSummary(totals) {
  const requests = Number(totals.requests) || 0;
  const errors = Number(totals.errors) || 0;
  const successRate = requests ? `${(((requests - errors) / requests) * 100).toFixed(1)}%` : "—";

  $("summaryCards").innerHTML = [
    metricCard("Total requests", fmtNum(requests), `${fmtNum(errors)} failed requests`, metricIcons.requests),
    metricCard(
      "Total tokens",
      fmtNum(totals.tokens),
      `${fmtNum(totals.input_tokens)} input · ${fmtNum(totals.output_tokens)} output`,
      metricIcons.tokens,
    ),
    metricCard("Estimated cost", fmtUsd(totals.cost_usd), "Reported by the gateway", metricIcons.cost),
    metricCard("Average latency", requests ? `${fmtNum(Math.round(totals.avg_latency_ms))} ms` : "—", "Across logged requests", metricIcons.latency),
    metricCard("Success rate", successRate, requests ? `${fmtNum(errors)} failed requests` : "No requests in this period", metricIcons.success),
  ].join("");
}

function setChartState(id, message) {
  const state = $(`${id}State`);
  state.textContent = message;
  state.hidden = false;
  $(id).hidden = true;
}

function clearChartState(id) {
  $(`${id}State`).hidden = true;
  $(id).hidden = false;
}

function chartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#111c2c",
        borderColor: "#293a53",
        borderWidth: 1,
        titleColor: "#edf3ff",
        bodyColor: "#c5d1e2",
        padding: 10,
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: "#8290a8", maxTicksLimit: 7, maxRotation: 0, font: { size: 9 } },
      },
      y: {
        beginAtZero: true,
        border: { display: false },
        grid: { color: "rgba(139, 162, 194, 0.09)", drawTicks: false },
        ticks: { color: "#8290a8", maxTicksLimit: 5, padding: 9, font: { size: 9 } },
      },
    },
  };
}

function renderCostChart(rows) {
  costChart?.destroy();
  costChart = undefined;
  if (!rows.length) {
    setChartState("costChart", "No cost data recorded in this period.");
    return;
  }
  if (!window.Chart) {
    setChartState("costChart", "Chart library unavailable. Check your connection and reload.");
    return;
  }

  clearChartState("costChart");
  const context = $("costChart").getContext("2d");
  const fill = context.createLinearGradient(0, 0, 0, 230);
  fill.addColorStop(0, "rgba(81, 139, 255, 0.28)");
  fill.addColorStop(1, "rgba(81, 139, 255, 0.015)");
  const base = chartOptions();
  costChart = new Chart(context, {
    type: "line",
    data: {
      labels: rows.map((row) => row.date),
      datasets: [{
        label: "Estimated cost",
        data: rows.map((row) => row.cost_usd),
        borderColor: "#6398ff",
        backgroundColor: fill,
        borderWidth: 2,
        pointRadius: rows.length > 35 ? 0 : 2.5,
        pointHoverRadius: 4,
        pointBackgroundColor: "#75a5ff",
        fill: true,
        tension: 0.34,
      }],
    },
    options: {
      ...base,
      interaction: { intersect: false, mode: "index" },
      scales: {
        ...base.scales,
        y: {
          ...base.scales.y,
          ticks: {
            ...base.scales.y.ticks,
            callback: (value) => `$${Number(value).toFixed(value < 1 ? 2 : 0)}`,
          },
        },
      },
      plugins: {
        ...base.plugins,
        tooltip: { ...base.plugins.tooltip, callbacks: { label: (context) => ` ${fmtUsd(context.parsed.y)}` } },
      },
    },
  });
}

function renderProviderChart(rows) {
  providerChart?.destroy();
  providerChart = undefined;
  const legend = $("providerLegend");
  legend.replaceChildren();

  if (!rows.length) {
    setChartState("providerChart", "No provider request data recorded.");
    return;
  }
  if (!window.Chart) {
    setChartState("providerChart", "Chart library unavailable. Check your connection and reload.");
    return;
  }

  clearChartState("providerChart");
  const sortedRows = [...rows].sort((a, b) => b.requests - a.requests);
  const total = sortedRows.reduce((sum, row) => sum + row.requests, 0);
  providerChart = new Chart($("providerChart"), {
    type: "doughnut",
    data: {
      labels: sortedRows.map((row) => row.key),
      datasets: [{
        data: sortedRows.map((row) => row.requests),
        backgroundColor: sortedRows.map((_, index) => chartColors[index % chartColors.length]),
        borderColor: "#101a2a",
        borderWidth: 3,
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "72%",
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#111c2c",
          borderColor: "#293a53",
          borderWidth: 1,
          titleColor: "#edf3ff",
          bodyColor: "#c5d1e2",
          callbacks: { label: (context) => ` ${context.label}: ${fmtNum(context.parsed)} requests` },
        },
      },
    },
    plugins: [{
      id: "providerCenterText",
      afterDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;
        const x = (chartArea.left + chartArea.right) / 2;
        const y = (chartArea.top + chartArea.bottom) / 2;
        ctx.save();
        ctx.textAlign = "center";
        ctx.fillStyle = "#edf3ff";
        ctx.font = "600 17px 'Space Grotesk', sans-serif";
        ctx.fillText(fmtNum(total), x, y);
        ctx.fillStyle = "#8290a8";
        ctx.font = "10px 'DM Sans', sans-serif";
        ctx.fillText("requests", x, y + 16);
        ctx.restore();
      },
    }],
  });

  legend.innerHTML = sortedRows.map((row, index) => `
    <div class="provider-item">
      <span class="provider-swatch" style="background:${chartColors[index % chartColors.length]}"></span>
      <span class="provider-name" title="${esc(row.key)}">${esc(row.key)}</span>
      <span class="provider-count">${fmtNum(row.requests)} · ${total ? ((row.requests / total) * 100).toFixed(1) : 0}%</span>
    </div>`).join("");
}

function renderTokenChart(rows) {
  tokenChart?.destroy();
  tokenChart = undefined;
  if (!rows.length) {
    setChartState("tokenChart", "No token usage recorded in this period.");
    return;
  }
  if (!window.Chart) {
    setChartState("tokenChart", "Chart library unavailable. Check your connection and reload.");
    return;
  }

  clearChartState("tokenChart");
  const base = chartOptions();
  tokenChart = new Chart($("tokenChart"), {
    type: "bar",
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { label: "Input tokens", data: rows.map((row) => row.input_tokens), backgroundColor: "#518bff", borderRadius: 3, maxBarThickness: 13 },
        { label: "Output tokens", data: rows.map((row) => row.output_tokens), backgroundColor: "#9370ff", borderRadius: 3, maxBarThickness: 13 },
      ],
    },
    options: {
      ...base,
      interaction: { intersect: false, mode: "index" },
      scales: {
        ...base.scales,
        x: { ...base.scales.x, stacked: false, ticks: { ...base.scales.x.ticks, maxTicksLimit: 6 } },
      },
      plugins: {
        ...base.plugins,
        tooltip: { ...base.plugins.tooltip, callbacks: { label: (context) => ` ${context.dataset.label}: ${fmtNum(context.parsed.y)}` } },
      },
    },
  });
}

function renderModels(rows) {
  const list = $("modelList");
  if (!rows.length) {
    list.innerHTML = '<p class="table-empty">No model usage recorded in this period.</p>';
    return;
  }

  const sortedRows = [...rows].sort((a, b) => b.requests - a.requests);
  const maxRequests = Math.max(...sortedRows.map((row) => row.requests));
  list.innerHTML = sortedRows.slice(0, 6).map((row, index) => `
    <div class="model-row" title="${esc(row.key)}: ${fmtNum(row.requests)} requests, ${fmtNum(row.tokens)} tokens">
      <span class="model-name">${esc(row.key)}</span>
      <span class="model-track"><span class="model-fill model-fill-${index % 5}" style="width:${maxRequests ? (row.requests / maxRequests) * 100 : 0}%"></span></span>
      <span class="model-count">${fmtNum(row.requests)}</span>
    </div>`).join("");
}

function renderRecent(rows) {
  const body = $("recentRows");
  $("requestCount").textContent = `${fmtNum(rows.length)}${rows.length === 50 ? " latest" : ""}`;
  $("recentEmpty").hidden = rows.length > 0;
  if (!rows.length) {
    body.replaceChildren();
    return;
  }

  body.innerHTML = rows.map((row) => {
    const date = new Date(row.created_at);
    const time = Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
    const status = row.status === "ok" ? "Success" : "Error";
    return `
      <tr>
        <td class="request-time">${esc(time)}</td>
        <td>${esc(row.provider)}</td>
        <td class="request-model" title="${esc(row.model)}">${esc(row.model)}</td>
        <td class="numeric">${fmtNum(row.tokens)}</td>
        <td class="numeric">${fmtUsd(row.cost_usd)}</td>
        <td class="numeric">${fmtNum(row.latency_ms)} ms</td>
        <td><span class="status-pill${row.status === "ok" ? "" : " error"}">${status}</span></td>
      </tr>`;
  }).join("");
}

function renderDashboard(data) {
  renderSummary(data.totals);
  renderCostChart(data.timeseries);
  renderProviderChart(data.by_provider);
  renderTokenChart(data.timeseries);
  renderModels(data.by_model);
  renderRecent(data.recent);
  $("costPeriod").textContent = `Last ${data.window_days} days`;
  $("content").hidden = false;
  $("notice").hidden = true;
}

async function load(event) {
  event?.preventDefault();
  const key = $("adminKey").value.trim();
  const button = $("refresh");
  const buttonLabel = button.querySelector("span");
  if (!key) {
    $("content").hidden = true;
    setNotice("Enter the admin API key configured on the server to load gateway analytics.");
    $("adminKey").focus();
    return;
  }

  const previousLabel = buttonLabel.textContent;
  button.disabled = true;
  buttonLabel.textContent = "Loading…";
  setNotice("Loading gateway analytics…");

  try {
    const headers = new Headers();
    headers.set("Authorization", `Bearer ${key}`);
    const response = await fetch(`/api/stats?days=${encodeURIComponent($("days").value)}`, {
      method: "GET",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });

    if (response.status === 401 || response.status === 403) {
      $("adminKey").value = "";
      $("content").hidden = true;
      setNotice("Admin API key rejected. Enter the exact ADMIN_API_KEY configured on the running server.", "error");
      return;
    }
    if (!response.ok) {
      throw new Error(`Request failed (${response.status} ${response.statusText}).`);
    }

    const data = await response.json();
    if (!data?.totals || !Array.isArray(data.timeseries) || !Array.isArray(data.by_provider) || !Array.isArray(data.by_model) || !Array.isArray(data.recent)) {
      throw new Error("The stats response did not contain the expected analytics data.");
    }
    renderDashboard(data);
  } catch (error) {
    $("content").hidden = true;
    setNotice(error instanceof Error ? `Could not load analytics: ${error.message}` : "Could not load analytics. Check the server and try again.", "error");
  } finally {
    button.disabled = false;
    buttonLabel.textContent = previousLabel;
  }
}

$("dashboardForm").addEventListener("submit", load);
$("days").addEventListener("change", () => {
  if ($("adminKey").value.trim()) load();
});
