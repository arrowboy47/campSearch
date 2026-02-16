function buildSearchParams() {
  const params = new URLSearchParams();

  const searchInput = document.getElementById("searchInput");
  const query = searchInput ? searchInput.value.trim() : "";
  if (query) {
    params.set("query", query);
  }

  const isOpenEl = document.querySelector('input[name="is_open"]');
  const hasWaterEl = document.querySelector('input[name="has_water"]');
  const hasRestroomsEl = document.querySelector('input[name="has_restrooms"]');
  const forestEl = document.getElementById("forest");
  const startEl = document.getElementById("start_date");
  const endEl = document.getElementById("end_date");

  if (isOpenEl && isOpenEl.checked) {
    params.set("is_open", "true");
  }
  if (hasWaterEl && hasWaterEl.checked) {
    params.set("has_water", "true");
  }
  if (hasRestroomsEl && hasRestroomsEl.checked) {
    params.set("has_restrooms", "true");
  }
  if (forestEl && forestEl.value) {
    params.set("forest", forestEl.value);
  }

   if (startEl && startEl.value) {
    params.set("start", startEl.value);
  }

  if (endEl && endEl.value) {
    params.set("end", endEl.value);
  }

  return params;
}

async function handleSearchSubmit(event) {
  event.preventDefault();

  const params = buildSearchParams();

  try {
    const res = await fetch(`/api/search?${params.toString()}`);
    const data = await res.json();

    if (res.status === 404 || !Array.isArray(data) || data.length === 0) {
      alert("No campsites found for that combination of filters.");
      return;
    }

    const qs = params.toString();

    if (data.length === 1) {
      window.location.href = qs
        ? `/campsite/${data[0].id}?${qs}`
        : `/campsite/${data[0].id}`;
    } else {
      window.location.href = qs ? `/results?${qs}` : "/results";
    }
  } catch (err) {
    console.error("Search failed:", err);
    alert("Something went wrong while searching.");
  }
}

document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("searchForm");
  if (form) {
    form.addEventListener("submit", handleSearchSubmit);
  }

  const searchInput = document.getElementById("searchInput");
  const filters = document.getElementById("filters");

  if (searchInput && filters) {
    searchInput.addEventListener("focus", function () {
      filters.style.display = "block";
    });
  }
});
