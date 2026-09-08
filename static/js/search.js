// Home-page search. The form is a plain GET to /results and works with JS off;
// this handler just adds a shortcut: if the current filters resolve to exactly
// one campsite, jump straight to its detail page instead of a 1-row results list.

function formParams(form) {
  // FormData picks up every named control, including the multi-value
  // checkboxes (terrain / activities / water_feature). Empty values are
  // dropped so the URL stays clean.
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form).entries()) {
    if (value !== "" && value != null) {
      params.append(key, value);
    }
  }
  return params;
}

async function handleSearchSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const params = formParams(form);
  const qs = params.toString();

  try {
    const res = await fetch(`/api/search?${qs}`);
    const data = await res.json();

    if (res.ok && Array.isArray(data) && data.length === 1) {
      window.location.href = qs
        ? `/campsite/${data[0].id}?${qs}`
        : `/campsite/${data[0].id}`;
      return;
    }
  } catch (err) {
    console.error("Search shortcut failed, falling back to results page:", err);
  }

  // Anything else (many matches, none, or an error): let the results page render.
  window.location.href = qs ? `/results?${qs}` : "/results";
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
