/**
 * Smart Shelf Spoilage Risk Index & Forecasting Dashboard
 * Uses Chart.js with date-fns time adapter.
 */

let chartInstance = null;
let currentDeviceId = null;
let pollTimer = null;

// DOM Elements
const deviceSelect = document.getElementById("device-select");
const refreshBtn = document.getElementById("refresh-btn");
const currentSriVal = document.getElementById("current-sri-value");
const sriRiskBadge = document.getElementById("sri-risk-badge");
const commodityVal = document.getElementById("commodity-value");
const trendLabel = document.getElementById("trend-label");
const trendRate = document.getElementById("trend-rate");
const lastUpdatedTime = document.getElementById("last-updated-time");
const insufficientBanner = document.getElementById("insufficient-data-banner");
const fanLegend = document.getElementById("fan-threshold-legend");
const alertLegend = document.getElementById("alert-threshold-legend");

/**
 * Initialize Chart.js configuration.
 */
function initChart() {
  const ctx = document.getElementById("sri-chart").getContext("2d");
  chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Historical SRI",
          data: [],
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.08)",
          fill: true,
          tension: 0.15,
          pointRadius: 2.5,
          pointHoverRadius: 5,
          borderWidth: 2,
        },
        {
          label: "Forecast Predicted SRI",
          data: [],
          borderColor: "#fbbf24",
          backgroundColor: "transparent",
          borderDash: [6, 6],
          fill: false,
          tension: 0,
          pointRadius: 3.5,
          pointHoverRadius: 6,
          pointBackgroundColor: "#fbbf24",
          borderWidth: 2,
        },
        {
          label: "Fan ON Threshold",
          data: [],
          borderColor: "rgba(192, 132, 252, 0.7)",
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
          borderWidth: 1.5,
        },
        {
          label: "Alert Threshold",
          data: [],
          borderColor: "rgba(248, 113, 113, 0.8)",
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          display: false, // Custom legend in HTML
        },
        tooltip: {
          callbacks: {
            label: function (context) {
              const label = context.dataset.label || "";
              const val = context.parsed.y;
              return `${label}: ${val !== null ? val.toFixed(3) : "--"}`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: {
            tooltipFormat: "PPpp",
            displayFormats: {
              minute: "HH:mm",
              hour: "HH:mm",
            },
          },
          grid: {
            color: "rgba(51, 65, 85, 0.6)",
          },
          ticks: {
            color: "#94a3b8",
            maxRotation: 0,
          },
        },
        y: {
          min: 0.0,
          suggestedMax: 1.0,
          grid: {
            color: "rgba(51, 65, 85, 0.6)",
          },
          ticks: {
            color: "#94a3b8",
            callback: function (val) {
              return val.toFixed(2);
            },
          },
          title: {
            display: true,
            text: "Spoilage Risk Index (0.0 - 1.0)",
            color: "#94a3b8",
          },
        },
      },
    },
  });
}

/**
 * Fetch available devices and populate dropdown.
 */
async function loadDevices() {
  try {
    const res = await fetch("/devices");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const devices = await res.json();

    deviceSelect.innerHTML = "";
    if (devices.length === 0) {
      deviceSelect.innerHTML = '<option value="">No devices registered</option>';
      return;
    }

    devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.device_id;
      opt.textContent = `${d.device_id} (${d.location || "Default Location"})`;
      deviceSelect.appendChild(opt);
    });

    currentDeviceId = devices[0].device_id;
    deviceSelect.value = currentDeviceId;
    await fetchDashboardData();
  } catch (err) {
    console.error("Failed to load devices:", err);
    deviceSelect.innerHTML = '<option value="">Error loading devices</option>';
  }
}

/**
 * Fetch historical readings and forecast data for current device.
 */
async function fetchDashboardData() {
  if (!currentDeviceId) return;

  const now = new Date();
  const sixHoursAgo = new Date(now.getTime() - 6 * 3600 * 1000).toISOString();

  try {
    // 1. Fetch history from existing endpoint and forecast in parallel
    const [historyRes, forecastRes] = await Promise.all([
      fetch(`/devices/${currentDeviceId}/history?start_time=${encodeURIComponent(sixHoursAgo)}&limit=500`),
      fetch(`/devices/${currentDeviceId}/forecast?horizon_minutes=60&step_minutes=5`),
    ]);

    const historyData = historyRes.ok ? await historyRes.json() : [];
    const forecastData = forecastRes.ok ? await forecastRes.json() : null;

    updateDashboardView(historyData, forecastData);
  } catch (err) {
    console.error("Error fetching dashboard telemetry:", err);
  }
}

/**
 * Update UI cards and Chart.js datasets in place.
 */
function updateDashboardView(historyReadings, forecast) {
  // Sort historical readings chronologically (API returns descending)
  const sortedHistory = [...historyReadings].sort(
    (a, b) => new Date(a.device_timestamp) - new Date(b.device_timestamp)
  );

  const historyPoints = sortedHistory
    .filter((r) => r.spoilage_index !== null && r.spoilage_index !== undefined)
    .map((r) => ({
      x: new Date(r.device_timestamp),
      y: Number(r.spoilage_index),
    }));

  let forecastPoints = [];
  const fanThresh = (forecast && forecast.fan_threshold) || 0.60;
  const alertThresh = (forecast && forecast.alert_threshold) || 0.70;

  // Status card: Commodity
  if (forecast && forecast.commodity) {
    commodityVal.textContent = forecast.commodity.replace("_", " ").toUpperCase();
  } else {
    commodityVal.textContent = "Unassigned";
  }

  // Status card: Current SRI
  let currentSri = null;
  if (forecast && forecast.current_sri !== null && forecast.current_sri !== undefined) {
    currentSri = forecast.current_sri;
  } else if (historyPoints.length > 0) {
    currentSri = historyPoints[historyPoints.length - 1].y;
  }

  if (currentSri !== null) {
    currentSriVal.textContent = Number(currentSri).toFixed(3);
    if (currentSri >= alertThresh) {
      sriRiskBadge.textContent = "HIGH RISK";
      sriRiskBadge.className = "badge badge-danger";
    } else if (currentSri >= fanThresh) {
      sriRiskBadge.textContent = "FAN ACTIVE";
      sriRiskBadge.className = "badge badge-caution";
    } else {
      sriRiskBadge.textContent = "NORMAL";
      sriRiskBadge.className = "badge badge-normal";
    }
  } else {
    currentSriVal.textContent = "--";
    sriRiskBadge.textContent = "NO DATA";
    sriRiskBadge.className = "badge badge-neutral";
  }

  // Status card: Trend
  if (!forecast || forecast.insufficient_data) {
    trendLabel.textContent = "Gathering data...";
    trendLabel.className = "badge badge-neutral";
    trendRate.textContent = "";
    insufficientBanner.style.display = "flex";
  } else {
    insufficientBanner.style.display = "none";
    const slope = forecast.trend_slope_per_min;
    if (slope > 0.001) {
      trendLabel.textContent = "Rising ↑";
      trendLabel.className = "badge badge-danger";
      trendRate.textContent = `(+${slope.toFixed(4)}/min)`;
    } else if (slope < -0.001) {
      trendLabel.textContent = "Falling ↓";
      trendLabel.className = "badge badge-normal";
      trendRate.textContent = `(${slope.toFixed(4)}/min)`;
    } else {
      trendLabel.textContent = "Stable →";
      trendLabel.className = "badge badge-neutral";
      trendRate.textContent = `(${slope >= 0 ? "+" : ""}${slope.toFixed(4)}/min)`;
    }

    // Build forecast series
    if (forecast.forecast && forecast.forecast.length > 0) {
      forecastPoints = forecast.forecast.map((pt) => ({
        x: new Date(pt.timestamp),
        y: Number(pt.predicted_sri),
      }));
    }
  }

  // Last Updated
  lastUpdatedTime.textContent = new Date().toLocaleTimeString();

  // Threshold Guide Lines across visible time window
  let minTime = new Date(Date.now() - 6 * 3600 * 1000);
  let maxTime = new Date(Date.now() + 60 * 60 * 1000);

  if (historyPoints.length > 0) {
    minTime = historyPoints[0].x;
  }
  if (forecastPoints.length > 0) {
    maxTime = forecastPoints[forecastPoints.length - 1].x;
  }

  const fanGuidePoints = [
    { x: minTime, y: fanThresh },
    { x: maxTime, y: fanThresh },
  ];
  const alertGuidePoints = [
    { x: minTime, y: alertThresh },
    { x: maxTime, y: alertThresh },
  ];

  fanLegend.textContent = `Fan ON Threshold (${fanThresh.toFixed(2)})`;
  alertLegend.textContent = `Alert Threshold (${alertThresh.toFixed(2)})`;

  // Update Chart.js datasets in place
  if (chartInstance) {
    chartInstance.data.datasets[0].data = historyPoints;
    chartInstance.data.datasets[1].data = forecastPoints;
    chartInstance.data.datasets[2].data = fanGuidePoints;
    chartInstance.data.datasets[3].data = alertGuidePoints;
    chartInstance.update();
  }
}

// Event Listeners
deviceSelect.addEventListener("change", (e) => {
  currentDeviceId = e.target.value;
  fetchDashboardData();
});

refreshBtn.addEventListener("click", () => {
  fetchDashboardData();
});

// Setup 45s Polling
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(fetchDashboardData, 45000);
}

// Init on DOM load
window.addEventListener("DOMContentLoaded", () => {
  initChart();
  loadDevices();
  startPolling();
});
