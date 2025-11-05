async function handleSearch(query) {
  try {
    const res = await fetch(`/api/search?query=${encodeURIComponent(query)}`);
    const data = await res.json();

    if (res.status === 404 || data.length === 0) {
      alert("No campsites found.");
      return;
    }

    // If only 1 match, redirect directly
    if (data.length === 1) {
      window.location.href = `/campsite/${data[0].id}`;
    } else {
      // Redirect to results page
      window.location.href = `/results?query=${encodeURIComponent(query)}`;
    }

  } catch (err) {
    console.error("Search failed:", err);
    alert("Something went wrong.");
  }
}

// Attach form submit event
document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("searchForm");

  form.addEventListener("submit", function (e) {
    e.preventDefault();  // Prevent actual page reload
    const input = document.getElementById("searchInput").value.trim();

    if (input) {
      handleSearch(input);
    }
  });
});