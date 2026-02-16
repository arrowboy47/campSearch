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

  fetch("/api/map/campsites")
    .then(async (resp) => {
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        console.error("/api/map/campsites returned an error status", resp.status, text);
        return [];
      }
      return resp.json();
    })
    .then((points) => {
      if (!Array.isArray(points) || !points.length) {
        console.warn("Map data loaded but no campsite points were returned.");
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
          const startEl = document.getElementById("start_date");
          const endEl = document.getElementById("end_date");

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

// Init on DOM ready ------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initMap();
});
