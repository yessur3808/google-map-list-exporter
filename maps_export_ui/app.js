const form = document.querySelector("#export-form");
const listUrlInput = document.querySelector("#list-url");
const outputPrefixInput = document.querySelector("#output-prefix");
const formatJsonInput = document.querySelector("#format-json");
const formatCsvInput = document.querySelector("#format-csv");
const urlShell = document.querySelector("#url-shell");
const urlError = document.querySelector("#url-error");
const clearUrlButton = document.querySelector("#clear-url");
const exportButton = document.querySelector("#export-button");
const emptyState = document.querySelector("#empty-state");
const progressView = document.querySelector("#progress-view");
const errorView = document.querySelector("#error-view");
const resultsView = document.querySelector("#results-view");
const stateBadge = document.querySelector("#state-badge");
const progressBar = document.querySelector("#progress-bar");
const progressList = document.querySelector("#progress-list");
const progressMessage = document.querySelector("#progress-message");
const progressStage = document.querySelector("#progress-stage");
const progressCount = document.querySelector("#progress-count");
const progressEta = document.querySelector("#progress-eta");
const errorMessage = document.querySelector("#error-message");
const resultListName = document.querySelector("#result-list-name");
const fileList = document.querySelector("#file-list");
const newExportButton = document.querySelector("#new-export-button");
const themeButton = document.querySelector("#theme-button");
const themeColor = document.querySelector('meta[name="theme-color"]');
const placeSearch = document.querySelector("#place-search");
const placeCount = document.querySelector("#place-count");
const placesEmpty = document.querySelector("#places-empty");
const placesEmptyMessage = document.querySelector("#places-empty-message");
const placesTableWrap = document.querySelector("#places-table-wrap");
const placesBody = document.querySelector("#places-body");
const mapCanvas = document.querySelector("#map-canvas");
const mapFrameShell = document.querySelector("#map-frame-shell");
const mapPlaceholder = document.querySelector("#map-placeholder");
const mapLocation = document.querySelector("#map-location");
const mapPulse = mapLocation.querySelector(".map-pulse");
const mapScanLine = document.querySelector("#map-scan-line");
const expandListButton = document.querySelector("#expand-list-button");
const expandMapButton = document.querySelector("#expand-map-button");
const appShell = document.querySelector(".app-shell");

let pollTimer = null;
let latestPlaces = [];
let activePlaceKey = "";
let renderedPlaceSignature = "";
let map = null;
let mapMarker = null;
let extractionStartedAt = null;
let extractionStartCount = 0;

const DEFAULTS_STORAGE_KEY = "maps-export-defaults";
const THEME_STORAGE_KEY = "maps-export-theme-preference";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const API_BASE_URL = String(window.MAPS_EXPORT_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");

function apiUrl(path) {
  return `${API_BASE_URL}${path}`;
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }
}

function setPanel(name) {
  emptyState.hidden = name !== "empty";
  progressView.hidden = name !== "progress";
  errorView.hidden = name !== "error";
  resultsView.hidden = name !== "results";
}

function setBadge(label, state) {
  stateBadge.textContent = label;
  stateBadge.className = `state-badge ${state}`;
}

function setTheme(theme, persist = false) {
  document.documentElement.dataset.theme = theme;
  if (persist) localStorage.setItem(THEME_STORAGE_KEY, theme);
  themeButton.setAttribute(
    "aria-label",
    theme === "dark" ? "Use light theme" : "Use dark theme",
  );
  themeColor.content = theme === "dark" ? "#151714" : "#f4f1ea";
}

function saveDefaults() {
  localStorage.setItem(DEFAULTS_STORAGE_KEY, JSON.stringify({
    outputPrefix: outputPrefixInput.value,
    json: formatJsonInput.checked,
    csv: formatCsvInput.checked,
  }));
}

function restoreDefaults() {
  try {
    const defaults = JSON.parse(localStorage.getItem(DEFAULTS_STORAGE_KEY));
    if (!defaults || typeof defaults !== "object") return;
    if (typeof defaults.outputPrefix === "string") {
      outputPrefixInput.value = defaults.outputPrefix;
    }
    if (typeof defaults.json === "boolean") formatJsonInput.checked = defaults.json;
    if (typeof defaults.csv === "boolean") formatCsvInput.checked = defaults.csv;
  } catch {
    localStorage.removeItem(DEFAULTS_STORAGE_KEY);
  }
}

function placeKey(place) {
  return place.google_maps_url || `${place.name}-${place.address}`;
}

function initializeMap(latitude, longitude) {
  mapCanvas.hidden = false;
  mapPlaceholder.hidden = true;
  map = window.L.map(mapCanvas, { zoomControl: true }).setView([latitude, longitude], 15);
  window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const icon = window.L.divIcon({ className: "", html: '<div class="map-place-marker"></div>', iconSize: [18, 18], iconAnchor: [9, 18] });
  mapMarker = window.L.marker([latitude, longitude], { icon }).addTo(map);
}

function travelMap(latitude, longitude) {
  if (!map) {
    initializeMap(latitude, longitude);
    return;
  }
  mapMarker.setLatLng([latitude, longitude]);
  map.flyTo([latitude, longitude], 15, { animate: true, duration: 2.4, easeLinearity: 0.2 });
}

function showPlaceOnMap(place, live = false) {
  if (!place) return;
  const key = placeKey(place);
  const coordinatesAvailable = Number.isFinite(place.latitude)
    && Number.isFinite(place.longitude);
  if (coordinatesAvailable && key !== activePlaceKey) {
    travelMap(place.latitude, place.longitude);
    activePlaceKey = key;
  }

  const name = mapLocation.querySelector("strong");
  const detail = mapLocation.querySelector("div > span");
  name.textContent = place.name || "Unnamed place";
  detail.textContent = place.address || place.category || "Google Maps location";
  mapPulse.classList.toggle("live", live);
  mapScanLine.hidden = !live;

  for (const row of placesBody.querySelectorAll("tr")) {
    row.classList.toggle("active", row.dataset.key === key);
  }
}

function createText(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value || "—";
  return element;
}

function renderPlaces(places) {
  latestPlaces = places || [];
  const filter = placeSearch.value.trim().toLowerCase();
  const visible = latestPlaces.filter((place) => [
    place.name,
    place.category,
    place.address,
    place.phone,
  ].some((value) => String(value || "").toLowerCase().includes(filter)));
  const signature = `${filter}|${latestPlaces.length}|${visible.map(placeKey).join("|")}`;

  placeCount.textContent = `${latestPlaces.length} ${latestPlaces.length === 1 ? "place" : "places"}`;
  placesEmpty.hidden = visible.length > 0;
  placesEmptyMessage.textContent = latestPlaces.length > 0
    ? "No places match this filter."
    : "Place details will appear as they are scraped.";
  placesTableWrap.hidden = visible.length === 0;

  if (signature === renderedPlaceSignature) return;
  renderedPlaceSignature = signature;
  placesBody.replaceChildren();

  for (const place of visible) {
    const row = document.createElement("tr");
    row.dataset.key = placeKey(place);
    row.tabIndex = 0;
    row.setAttribute("aria-label", `Show ${place.name || "place"} on map`);

    const identityCell = document.createElement("td");
    identityCell.append(
      createText("strong", "place-name", place.name || "Unnamed place"),
      createText("span", "place-address", place.address),
      createText("span", "place-hours", place.opening_hours),
    );

    const categoryCell = document.createElement("td");
    categoryCell.textContent = place.category || "—";

    const ratingCell = createText(
      "td",
      "rating-cell",
      place.rating ? `${place.rating} / 5` : "—",
    );

    const contactCell = document.createElement("td");
    contactCell.append(
      createText("span", "place-contact", place.phone),
      createText("span", "place-contact", place.website),
    );

    const linkCell = document.createElement("td");
    const mapsLink = document.createElement("a");
    mapsLink.className = "place-link";
    mapsLink.href = place.google_maps_url;
    mapsLink.target = "_blank";
    mapsLink.rel = "noreferrer";
    mapsLink.title = `Open ${place.name || "place"} in Google Maps`;
    mapsLink.setAttribute("aria-label", mapsLink.title);
    mapsLink.innerHTML = '<i data-lucide="external-link" aria-hidden="true"></i>';
    mapsLink.addEventListener("click", (event) => event.stopPropagation());
    linkCell.append(mapsLink);

    const selectPlace = () => showPlaceOnMap(place, false);
    row.addEventListener("click", selectPlace);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectPlace();
      }
    });
    row.append(identityCell, categoryCell, ratingCell, contactCell, linkCell);
    placesBody.append(row);
  }
  refreshIcons();
}

function updateLiveData(job) {
  renderPlaces(job.places || []);
  if (job.current_place) {
    showPlaceOnMap(job.current_place, job.status === "running");
  } else if (job.status !== "running") {
    mapPulse.classList.remove("live");
    mapScanLine.hidden = true;
  }
}

function resetLiveData() {
  latestPlaces = [];
  activePlaceKey = "";
  renderedPlaceSignature = "";
  placeSearch.value = "";
  placesBody.replaceChildren();
  placeCount.textContent = "0 places";
  placesEmpty.hidden = false;
  placesEmptyMessage.textContent = "Place details will appear as they are scraped.";
  placesTableWrap.hidden = true;
  if (map) {
    map.stop();
    map.remove();
    map = null;
    mapMarker = null;
  }
  mapCanvas.hidden = true;
  mapPlaceholder.hidden = false;
  mapPulse.classList.remove("live");
  mapScanLine.hidden = true;
  mapLocation.querySelector("strong").textContent = "Waiting for a place";
  mapLocation.querySelector("div > span").textContent = "The map will follow the scraper";
  progressEta.textContent = "Estimating time";
  extractionStartedAt = null;
  extractionStartCount = 0;
}

function formatRemainingTime(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `About ${minutes} min remaining`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `About ${hours} hr${remainder ? ` ${remainder} min` : ""} remaining`;
}

function updateEta(job) {
  if (job.stage !== "extracting" || !job.total || job.current >= job.total) {
    progressEta.textContent = job.status === "completed" ? "Finished" : "Estimating time";
    return;
  }
  if (extractionStartedAt === null) {
    extractionStartedAt = Date.now();
    extractionStartCount = job.current;
  }
  const completed = job.current - extractionStartCount;
  const elapsedSeconds = (Date.now() - extractionStartedAt) / 1000;
  if (completed < 1 || elapsedSeconds < 4) {
    progressEta.textContent = "Estimating time";
    return;
  }
  const remainingSeconds = (elapsedSeconds / completed) * (job.total - job.current);
  progressEta.textContent = formatRemainingTime(remainingSeconds);
}

function validateUrl() {
  const value = listUrlInput.value.trim();
  let valid = false;

  try {
    const url = new URL(value);
    valid = url.protocol === "https:" && [
      "maps.app.goo.gl",
      "www.google.com",
      "google.com",
    ].includes(url.hostname);
  } catch {
    valid = false;
  }

  urlShell.classList.toggle("invalid", !valid && value.length > 0);
  urlError.textContent = valid || !value ? "" : "Enter a valid Google Maps share URL.";
  return valid;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderProgress(job) {
  setPanel("progress");
  setBadge("Running", "running");
  progressList.textContent = job.list_name || "Opening list";
  progressMessage.textContent = job.message;
  progressStage.textContent = {
    starting: "Preparing",
    discovering: "Finding places",
    extracting: "Reading details",
  }[job.stage] || "Working";

  let percent = 7;
  if (job.total > 0) {
    const ratio = Math.min(job.current / job.total, 1);
    percent = job.stage === "discovering"
      ? 10 + ratio * 25
      : 35 + ratio * 63;
  }
  progressBar.style.width = `${percent}%`;
  progressCount.textContent = job.total ? `${job.current} / ${job.total}` : "";
  updateEta(job);
  updateLiveData(job);
}

function renderFiles(job) {
  fileList.replaceChildren();
  for (const file of job.files) {
    const row = document.createElement("div");
    row.className = "file-row";

    const type = document.createElement("span");
    type.className = `file-type ${file.format}`;
    type.textContent = file.format;

    const info = document.createElement("div");
    info.className = "file-info";
    const name = document.createElement("strong");
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement("span");
    size.textContent = formatBytes(file.size);
    info.append(name, size);

    const link = document.createElement("a");
    link.className = "download-button";
    link.href = apiUrl(file.url);
    link.download = file.name;
    link.title = `Download ${file.name}`;
    link.setAttribute("aria-label", `Download ${file.name}`);
    link.innerHTML = '<i data-lucide="download" aria-hidden="true"></i>';

    row.append(type, info, link);
    fileList.append(row);
  }
  refreshIcons();
}

function renderCompleted(job) {
  setPanel("results");
  setBadge("Complete", "completed");
  resultListName.textContent = job.list_name || "Maps list";
  renderFiles(job);
  updateLiveData(job);
  mapPulse.classList.remove("live");
  mapScanLine.hidden = true;
  progressEta.textContent = "Finished";
  exportButton.disabled = false;
}

function renderFailed(job) {
  setPanel("error");
  setBadge("Failed", "failed");
  errorMessage.textContent = job.error || "The export could not be completed.";
  updateLiveData(job);
  exportButton.disabled = false;
}

async function pollJob(jobId) {
  try {
    const response = await fetch(apiUrl(`/api/jobs/${jobId}`), { cache: "no-store" });
    if (!response.ok) throw new Error("Could not read export status.");
    const job = await response.json();

    if (job.status === "completed") {
      renderCompleted(job);
      return;
    }
    if (job.status === "failed") {
      renderFailed(job);
      return;
    }

    renderProgress(job);
    pollTimer = window.setTimeout(() => pollJob(jobId), 700);
  } catch (error) {
    renderFailed({ error: error.message });
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  urlError.textContent = "";

  if (!validateUrl()) {
    if (!listUrlInput.value.trim()) urlError.textContent = "Paste a Google Maps share URL.";
    urlShell.classList.add("invalid");
    listUrlInput.focus();
    return;
  }

  window.clearTimeout(pollTimer);
  resetLiveData();
  exportButton.disabled = true;
  setPanel("progress");
  setBadge("Running", "running");
  progressBar.style.width = "7%";
  progressList.textContent = "Opening list";
  progressMessage.textContent = "Starting Chromium";
  progressStage.textContent = "Preparing";
  progressCount.textContent = "";

  const formats = ["txt"];
  if (formatJsonInput.checked) formats.push("json");
  if (formatCsvInput.checked) formats.push("csv");
  saveDefaults();

  try {
    if (location.hostname.endsWith(".github.io") && !API_BASE_URL) {
      throw new Error("Set apiBaseUrl in config.js to the URL of the Python exporter service.");
    }
    const response = await fetch(apiUrl("/api/jobs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        list_url: listUrlInput.value.trim(),
        output_prefix: outputPrefixInput.value.trim(),
        formats,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not start export.");
    pollJob(payload.id);
  } catch (error) {
    renderFailed({ error: error.message });
  }
});

listUrlInput.addEventListener("input", () => {
  if (urlError.textContent) validateUrl();
});

clearUrlButton.addEventListener("click", () => {
  listUrlInput.value = "";
  urlError.textContent = "";
  urlShell.classList.remove("invalid");
  listUrlInput.focus();
});

function restoreSplitView() {
  appShell.classList.remove("map-expanded", "list-expanded");
  for (const [button, name] of [[expandMapButton, "map"], [expandListButton, "list"]]) {
    button.setAttribute("aria-label", `Expand ${name} view`);
    button.title = `Expand ${name} view`;
    button.innerHTML = '<i data-lucide="maximize-2" aria-hidden="true"></i>';
  }
  refreshIcons();
  window.setTimeout(() => map?.invalidateSize(), 0);
}

newExportButton.addEventListener("click", () => {
  window.clearTimeout(pollTimer);
  pollTimer = null;
  restoreSplitView();
  resetLiveData();
  setPanel("empty");
  setBadge("Ready", "idle");
  listUrlInput.focus();
});

themeButton.addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
});

function setExpandedView(view) {
  const className = `${view}-expanded`;
  const expanded = !appShell.classList.contains(className);
  appShell.classList.remove("map-expanded", "list-expanded");
  if (expanded) appShell.classList.add(className);

  for (const [button, name] of [[expandMapButton, "map"], [expandListButton, "list"]]) {
    const active = expanded && name === view;
    button.setAttribute("aria-label", active ? `Restore ${name} view` : `Expand ${name} view`);
    button.title = active ? "Restore split view" : `Expand ${name} view`;
    button.innerHTML = `<i data-lucide="${active ? "minimize-2" : "maximize-2"}" aria-hidden="true"></i>`;
  }
  refreshIcons();
  window.setTimeout(() => map?.invalidateSize(), 0);
}

expandMapButton.addEventListener("click", () => setExpandedView("map"));
expandListButton.addEventListener("click", () => setExpandedView("list"));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (appShell.classList.contains("map-expanded")
    || appShell.classList.contains("list-expanded"))) {
    restoreSplitView();
  }
});

outputPrefixInput.addEventListener("change", saveDefaults);
formatJsonInput.addEventListener("change", saveDefaults);
formatCsvInput.addEventListener("change", saveDefaults);

systemTheme.addEventListener("change", (event) => {
  if (!localStorage.getItem(THEME_STORAGE_KEY)) {
    setTheme(event.matches ? "dark" : "light");
  }
});

placeSearch.addEventListener("input", () => {
  renderedPlaceSignature = "";
  renderPlaces(latestPlaces);
});

window.addEventListener("DOMContentLoaded", () => {
  localStorage.removeItem("maps-export-theme");
  restoreDefaults();
  setTheme(document.documentElement.dataset.theme || "light");
  refreshIcons();
});
