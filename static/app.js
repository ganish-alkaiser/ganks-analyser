let hoursChart;
let weekdaysChart;

const regionSelect = document.getElementById("regionSelect");
const startDateInput = document.getElementById("startDateInput");
const endDateInput = document.getElementById("endDateInput");
const analyzeBtn = document.getElementById("analyzeBtn");
const statusEl = document.getElementById("status");
const preset7dBtn = document.getElementById("preset7d");
const preset30dBtn = document.getElementById("preset30d");
const presetMonthBtn = document.getElementById("presetMonth");
const presetClearBtn = document.getElementById("presetClear");

const killsSeenEl = document.getElementById("killsSeen");
const gankedFoundEl = document.getElementById("gankedFound");
const gankedWithTimeEl = document.getElementById("gankedWithTime");
const peaksEl = document.getElementById("peaks");
const sourceInfoEl = document.getElementById("sourceInfo");

async function loadRegions() {
  const response = await fetch("/api/regions");
  const payload = await response.json();
  regionSelect.innerHTML = "";

  const blankOption = document.createElement("option");
  blankOption.value = "";
  blankOption.textContent = "Select a region...";
  blankOption.selected = true;
  regionSelect.appendChild(blankOption);

  for (const region of payload.regions) {
    const option = document.createElement("option");
    option.value = region.id;
    option.textContent = `${region.name} (${region.id})`;
    regionSelect.appendChild(option);
  }
}

function renderCharts(data) {
  if (hoursChart) {
    hoursChart.destroy();
  }
  if (weekdaysChart) {
    weekdaysChart.destroy();
  }

  const hoursCtx = document.getElementById("hoursChart");
  const weekdaysCtx = document.getElementById("weekdaysChart");

  hoursChart = new Chart(hoursCtx, {
    type: "bar",
    data: {
      labels: data.hours.labels,
      datasets: [
        {
          label: "Kills GANKED",
          data: data.hours.data,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 },
        },
      },
    },
  });

  weekdaysChart = new Chart(weekdaysCtx, {
    type: "bar",
    data: {
      labels: data.weekdays.labels,
      datasets: [
        {
          label: "Kills GANKED",
          data: data.weekdays.data,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 },
        },
      },
    },
  });
}

function updateMetrics(data) {
  killsSeenEl.textContent = data.total_kills_seen;
  gankedFoundEl.textContent = data.ganked_in_period ?? data.ganked_kills_found;
  gankedWithTimeEl.textContent = data.ganked_with_timestamp;

  const topHour = data.hours.top ?? "-";
  const topWeekday = data.weekdays.top ?? "-";
  peaksEl.textContent = `${topHour} / ${topWeekday}`;

  const source = data.source || {};
  const pagesApi = source.pages_from_api ?? 0;
  const pagesCache = source.pages_from_cache ?? 0;
  const timesApi = source.kill_times_from_api ?? 0;
  const timesCache = source.kill_times_from_cache ?? 0;
  sourceInfoEl.textContent = `Pages API:${pagesApi} | Cache:${pagesCache} | Times API:${timesApi} | Cache:${timesCache}`;
}

function formatLocalDate(date) {
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setPeriod(startDate, endDate) {
  startDateInput.value = startDate;
  endDateInput.value = endDate;
}

function applyLastDays(days) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - (days - 1));
  setPeriod(formatLocalDate(start), formatLocalDate(end));
}

function applyCurrentMonth() {
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  setPeriod(formatLocalDate(start), formatLocalDate(today));
}

function clearPeriod() {
  setPeriod("", "");
}

async function analyze() {
  const regionId = regionSelect.value;
  const startDate = startDateInput.value;
  const endDate = endDateInput.value;

  if (!regionId) {
    statusEl.textContent = "Please select a region before running analysis.";
    return;
  }

  if (startDate && endDate && startDate > endDate) {
    statusEl.textContent = "Error: start date cannot be greater than end date.";
    return;
  }

  analyzeBtn.disabled = true;
  statusEl.textContent = "Fetching data from zKillboard...";

  try {
    const params = new URLSearchParams({
      region_id: regionId,
    });
    if (startDate) {
      params.set("start_date", startDate);
    }
    if (endDate) {
      params.set("end_date", endDate);
    }

    const response = await fetch(`/api/analysis?${params.toString()}`);
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || "Failed to analyze data");
    }

    updateMetrics(payload);
    renderCharts(payload);
    const periodText = payload.start_date || payload.end_date
      ? ` period: ${payload.start_date ?? "..."} to ${payload.end_date ?? "..."}`
      : " period: full range";
    statusEl.textContent = `Analysis completed for region ${payload.region_id} (scanned pages: ${payload.pages_scanned};${periodText}).`;
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    analyzeBtn.disabled = false;
  }
}

analyzeBtn.addEventListener("click", analyze);
preset7dBtn.addEventListener("click", () => applyLastDays(7));
preset30dBtn.addEventListener("click", () => applyLastDays(30));
presetMonthBtn.addEventListener("click", applyCurrentMonth);
presetClearBtn.addEventListener("click", clearPeriod);

loadRegions().then(() => {
  statusEl.textContent = "Select a region and click Analyze.";
}).catch((error) => {
  statusEl.textContent = `Error loading regions: ${error.message}`;
});
