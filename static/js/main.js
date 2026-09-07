// Theme handling ---------------------------------------------------------

const THEME_STORAGE_KEY = "campsearch-theme";

function getSystemTheme() {
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function updateThemeToggleUI(activeMode) {
  const buttons = document.querySelectorAll(".theme-toggle-btn");
  buttons.forEach((btn) => {
    const value = btn.dataset.themeChoice;
    if (!value) return;
    btn.classList.toggle("is-active", value === activeMode);
  });
}

function applyTheme(mode) {
  const root = document.documentElement;
  const effective = mode === "system" ? getSystemTheme() : mode;
  root.dataset.theme = effective;
  updateThemeToggleUI(mode);
}

function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_STORAGE_KEY);
  } catch (e) {
    stored = null;
  }

  const initialMode = stored || "system";
  applyTheme(initialMode);

  const mql = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  if (mql && typeof mql.addEventListener === "function") {
    mql.addEventListener("change", () => {
      const currentPref = localStorage.getItem(THEME_STORAGE_KEY) || "system";
      if (currentPref === "system") {
        applyTheme("system");
      }
    });
  }

  const buttons = document.querySelectorAll(".theme-toggle-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const value = btn.dataset.themeChoice;
      if (!value) return;
      try {
        localStorage.setItem(THEME_STORAGE_KEY, value);
      } catch (e) {
        // ignore storage errors
      }
      applyTheme(value);
    });
  });
}

// Map handling -----------------------------------------------------------

function initMap() {
  const mapEl = document.getElementById("map");
  if (!mapEl) {
    return;
  }

  if (typeof L === "undefined") {
    console.error("Leaflet library is not available; map cannot be initialized.");
    return;
  }

  const map = L.map(mapEl, {
    center: [37.3, -119.5],
    zoom: 5.5,
    scrollWheelZoom: true,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  // The results page embeds just its result set as JSON; the home page has no
  // embedded data and asks the API for every campsite.
  const embedded = document.getElementById("mapData");
  let source;
  if (embedded) {
    let parsed = [];
    try {
      parsed = JSON.parse(embedded.textContent || "[]");
    } catch (e) {
      console.error("Could not parse embedded #mapData", e);
    }
    source = Promise.resolve(parsed);
  } else {
    source = fetch("/api/map/campsites").then(async (resp) => {
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        console.error("/api/map/campsites returned an error status", resp.status, text);
        return [];
      }
      return resp.json();
    });
  }

  source
    .then((points) => {
      if (!Array.isArray(points) || !points.length) {
        console.warn("Map: no campsite points to plot.");
        return;
      }

      console.log("Loaded campsite points for map:", points.length);

      const forestColors = {};
      const colorPalette = ["#166534", "#92400e", "#0369a1", "#15803d", "#7c2d12", "#047857"];
      let paletteIndex = 0;

      const bounds = [];

      points.forEach((site) => {
        const lat = site.latitude;
        const lon = site.longitude;
        if (lat == null || lon == null) return;

        const forest = site.forest_name || "Other";
        if (!forestColors[forest]) {
          forestColors[forest] = colorPalette[paletteIndex % colorPalette.length];
          paletteIndex += 1;
        }
        const color = forestColors[forest];

        const marker = L.circleMarker([lat, lon], {
          radius: 5,
          color,
          weight: 1.2,
          fillColor: color,
          fillOpacity: 0.9,
        });

        // Hover tooltip: small "mini card" with name + link
        marker.bindTooltip(
          `<div class="map-tooltip-card">
             <div class="map-tooltip-title">${site.name}</div>
             <a class="map-tooltip-link" href="/campsite/${site.id}">Open campsite</a>
           </div>`,
          {
            direction: "top",
            offset: [0, -4],
            permanent: false,
            sticky: true,
            className: "map-tooltip",
          }
        );

        marker.on("click", () => {
          const params = new URLSearchParams();
          const startEl = document.querySelector('input[name="start"]');
          const endEl = document.querySelector('input[name="end"]');

          if (startEl && startEl.value) {
            params.set("start", startEl.value);
          }
          if (endEl && endEl.value) {
            params.set("end", endEl.value);
          }

          const qs = params.toString();
          window.location.href = qs
            ? `/campsite/${site.id}?${qs}`
            : `/campsite/${site.id}`;
        });

        marker.addTo(map);
        bounds.push([lat, lon]);
      });

      if (bounds.length) {
        map.fitBounds(bounds, { padding: [30, 30] });
      }
    })
    .catch((err) => {
      console.error("Failed to load map data", err);
    });
}

// Date range inputs ----------------------------------------------------
// Native <input type="date"> already gives a calendar popover; this just keeps
// the pair sane: no past dates, end never before start, and picking a start
// pre-fills an empty end.

function initDateRange() {
  const start = document.querySelector('input[name="start"]');
  const end = document.querySelector('input[name="end"]');
  if (!start || !end) return;

  const today = new Date().toISOString().slice(0, 10);
  start.min = today;
  end.min = start.value || today;

  start.addEventListener("change", () => {
    end.min = start.value || today;
    if (start.value && (!end.value || end.value < start.value)) {
      end.value = start.value;
    }
  });

  end.addEventListener("change", () => {
    if (start.value && end.value && end.value < start.value) {
      end.value = start.value;
    }
  });
}

// Copy-link button -----------------------------------------------------

function initShare() {
  const btn = document.querySelector("[data-copy-link]");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    const url = window.location.href;
    const label = btn.textContent;
    try {
      await navigator.clipboard.writeText(url);
      btn.textContent = "Link copied";
    } catch (e) {
      // Clipboard API unavailable (http, old browser): fall back to a prompt.
      window.prompt("Copy this link:", url);
    }
    setTimeout(() => {
      btn.textContent = label;
    }, 1800);
  });
}

// Location: "near me" + campsite driving distance ---------------------

function reloadWithCoords(pos) {
  const p = new URLSearchParams(window.location.search);
  p.set("lat", pos.coords.latitude.toFixed(5));
  p.set("lon", pos.coords.longitude.toFixed(5));
  window.location.search = p.toString();
}

function initNearby() {
  const btn = document.getElementById("useLocationBtn");
  if (!btn || !navigator.geolocation) return;
  btn.addEventListener("click", () => {
    btn.textContent = "Locating…";
    navigator.geolocation.getCurrentPosition(
      reloadWithCoords,
      () => {
        btn.textContent = "Location blocked — using home if set";
      },
      { timeout: 8000 }
    );
  });
}

function formatDrive(d) {
  let t = `${d.miles} mi`;
  if (d.minutes) {
    const h = Math.floor(d.minutes / 60);
    const m = String(d.minutes % 60).padStart(2, "0");
    t += ` · ~${h}h ${m}m`;
  }
  t += ` from ${d.label}`;
  if (d.estimated) t += " (estimated)";
  return t;
}

function initDrive() {
  const line = document.getElementById("driveLine");
  if (!line || !navigator.geolocation) return;
  const valueEl = document.getElementById("driveValue");
  const id = line.dataset.campsiteId;

  function fetchFor(lat, lon) {
    const qs = new URLSearchParams({ campsite_id: id });
    if (lat != null) {
      qs.set("lat", lat);
      qs.set("lon", lon);
    }
    fetch(`/api/distance?${qs.toString()}`)
      .then((r) => r.json())
      .then((d) => {
        if (d && d.available) valueEl.textContent = formatDrive(d);
      })
      .catch(() => {});
  }

  const ask = document.getElementById("driveAsk");
  if (ask) {
    ask.addEventListener("click", (e) => {
      e.preventDefault();
      valueEl.textContent = "locating…";
      navigator.geolocation.getCurrentPosition(
        (pos) => fetchFor(pos.coords.latitude.toFixed(5), pos.coords.longitude.toFixed(5)),
        () => {
          valueEl.textContent = "location unavailable";
        },
        { timeout: 8000 }
      );
    });
    return;
  }

  // Server already rendered a home-based distance; quietly check whether the
  // device is far enough from home that the server would switch to "your
  // location", and update in place if so.
  navigator.geolocation.getCurrentPosition(
    (pos) => fetchFor(pos.coords.latitude.toFixed(5), pos.coords.longitude.toFixed(5)),
    () => {},
    { timeout: 8000, maximumAge: 300000 }
  );
}

// Settings (gear) menu ----------------------------------------------

function initMenu() {
  const menu = document.querySelector("[data-menu]");
  if (!menu) return;
  const toggle = menu.querySelector("[data-menu-toggle]");
  const panel = menu.querySelector("[data-menu-panel]");
  if (!toggle || !panel) return;

  const close = () => {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
  };

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    panel.hidden ? open() : close();
  });
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !menu.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
}

// Init on DOM ready ------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initMenu();
  initMap();
  initDateRange();
  initShare();
  initNearby();
  initDrive();
});
