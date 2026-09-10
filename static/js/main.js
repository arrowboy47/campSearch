// Theme handling ---------------------------------------------------------

const THEME_STORAGE_KEY = "campsearch-theme";

// Escape text before it goes into an innerHTML string. Campsite names are
// third-party scraped data, so they must never be trusted as markup.
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

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

// A gold star for the signed-in user's saved home, a pin for the browser's
// current location. Both float above the campsite dots.
function addContextMarkers(map) {
  const star = L.divIcon({ className: "map-ctx-icon", html: "⭐", iconSize: [24, 24], iconAnchor: [12, 12] });
  const pin = L.divIcon({ className: "map-ctx-icon", html: "📍", iconSize: [24, 24], iconAnchor: [12, 24] });

  fetch("/api/me")
    .then((r) => r.json())
    .then((me) => {
      if (me && me.home) {
        L.marker([me.home.lat, me.home.lon], { icon: star, zIndexOffset: 1000, interactive: true })
          .bindTooltip("Home" + (me.home.label ? " · " + me.home.label : ""), { direction: "top" })
          .addTo(map);
      }
    })
    .catch(() => {});

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        L.marker([pos.coords.latitude, pos.coords.longitude], { icon: pin, zIndexOffset: 1000 })
          .bindTooltip("Your location", { direction: "top" })
          .addTo(map);
      },
      () => {},
      { timeout: 8000, maximumAge: 300000 }
    );
  }
}

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

  addContextMarkers(map);

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

      const bounds = [];

      points.forEach((site) => {
        const lat = site.latitude;
        const lon = site.longitude;
        if (lat == null || lon == null) return;

        const color = LAND_TYPE_COLORS[site.land_type] || LAND_TYPE_COLORS.other;

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
             <div class="map-tooltip-title">${esc(site.name)}</div>
             <a class="map-tooltip-link" href="/campsite/${encodeURIComponent(site.id)}">Open campsite</a>
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

// Land-type -> marker colour, shared by the homepage map and its legend.
const LAND_TYPE_COLORS = {
  national_park: "#15803d",
  national_forest: "#166534",
  state_park: "#0369a1",
  blm: "#b45309",
  local: "#7c3aed",
  other: "#6b7280",
};
const LAND_TYPE_LABELS = {
  national_park: "National Park",
  national_forest: "National Forest",
  state_park: "State Park",
  blm: "BLM",
  local: "County / Regional",
  other: "Other",
};

const APPROX_TITLE =
  "This campground publishes no exact location. Distance is measured from the " +
  "centre of its county, so it can be off by many miles.";

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

// Rebuild the drive line, re-adding the "approximate" tag when the API says the
// distance came from a county-level coordinate.
function renderDrive(valueEl, d) {
  valueEl.textContent = formatDrive(d);
  if (d.approximate) {
    const tag = document.createElement("span");
    tag.className = "approx-tag";
    tag.tabIndex = 0;
    tag.title = APPROX_TITLE;
    tag.textContent = "approximate";
    valueEl.append(" ", tag);
  }
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
        if (d && d.available) renderDrive(valueEl, d);
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

// "Add to collection": reveal the name box when "New collection" is picked.

function initCollectionAdd() {
  const sel = document.querySelector("[data-collection-select]");
  const nameInput = document.querySelector("[data-collection-new]");
  if (!sel || !nameInput) return;
  sel.addEventListener("change", () => {
    const isNew = sel.value === "__new";
    nameInput.hidden = !isNew;
    if (isNew) nameInput.focus();
  });
}

// Homepage "Campsites near you": if the server had no origin, quietly ask the
// browser for a location once and reload with it as ?lat=&lon=.

function initNearHome() {
  const el = document.querySelector("[data-near-locate]");
  if (!el || !navigator.geolocation) return;
  const params = new URLSearchParams(window.location.search);
  if (params.has("lat")) return; // already tried this page load
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      params.set("lat", pos.coords.latitude.toFixed(5));
      params.set("lon", pos.coords.longitude.toFixed(5));
      window.location.search = params.toString();
    },
    () => {},
    { timeout: 8000, maximumAge: 300000 }
  );
}

// Fold long prose behind a "Show more" toggle.

function initReadMore() {
  document.querySelectorAll("[data-readmore]").forEach((wrap) => {
    const btn = wrap.querySelector("[data-readmore-toggle]");
    const body = wrap.querySelector(".section-body");
    if (!btn || !body) return;
    if (body.scrollHeight <= 260) return; // short enough, leave it

    wrap.classList.add("is-clamped");
    btn.hidden = false;
    btn.addEventListener("click", () => {
      const clamped = wrap.classList.toggle("is-clamped");
      btn.textContent = clamped ? "Show more" : "Show less";
    });
  });
}

// Selection counter: ping the server when a result is opened from the list so
// popular campsites can float up in search scoring (migration 0017). Fire and
// forget — sendBeacon survives the page unload, and a failure changes nothing.

function initPickTracking() {
  document.querySelectorAll("[data-pick]").forEach((link) => {
    link.addEventListener("click", () => {
      const id = link.getAttribute("data-pick");
      if (!id) return;
      const url = `/api/campsite/${id}/pick`;
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url);
      } else {
        fetch(url, { method: "POST", keepalive: true }).catch(() => {});
      }
    });
  });
}

// Floating "sign in to save" prompt for anonymous visitors.
function initAuthGate() {
  const gate = document.getElementById("authGate");
  if (!gate) return;
  const open = () => {
    gate.hidden = false;
    document.body.style.overflow = "hidden";
  };
  const close = () => {
    gate.hidden = true;
    document.body.style.overflow = "";
  };
  document.querySelectorAll("[data-auth-gate]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      open();
    });
  });
  gate.querySelectorAll("[data-auth-gate-close]").forEach((el) =>
    el.addEventListener("click", close)
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !gate.hidden) close();
  });
}

// Single-marker map on a campsite page (real coordinates only).
function initSiteMap() {
  const el = document.getElementById("siteMap");
  if (!el || typeof L === "undefined") return;
  const lat = parseFloat(el.dataset.lat);
  const lon = parseFloat(el.dataset.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

  const map = L.map(el, { scrollWheelZoom: false, attributionControl: true }).setView(
    [lat, lon],
    12
  );
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  L.marker([lat, lon])
    .addTo(map)
    .bindPopup(esc(el.dataset.name || "Campsite"));
  // container starts hidden/zero-size in some layouts; nudge Leaflet to re-measure
  setTimeout(() => map.invalidateSize(), 0);
}

// Init on DOM ready ------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initMenu();
  initNearHome();
  initReadMore();
  initMap();
  initSiteMap();
  initAuthGate();
  initDateRange();
  initShare();
  initNearby();
  initDrive();
  initCollectionAdd();
  initPickTracking();
});
