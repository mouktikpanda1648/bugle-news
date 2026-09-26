/* ==========================================================================
   THE DAILY BUGLE - MY TRUST FILE CONTROLLER
   ========================================================================== */

async function loadMyTrustFile() {
  try {
    const res = await fetch("/api/reports/mine");
    if (!res.ok) {
      showToast("Couldn't load your trust file.", "error");
      return;
    }
    const data = await res.json();
    const user = data.user;

    document.getElementById("profile-name").textContent = user.name || user.username;
    document.getElementById("profile-meta").textContent = `@${user.username} · Member since sign-up`;

    const trustPct = Math.round((user.trust_score || 0) * 100);
    const ringEl = document.getElementById("profile-trust-value");
    ringEl.textContent = `${trustPct}`;
    ringEl.style.color = trustPct >= 70 ? "var(--bugle-green)" : trustPct >= 40 ? "var(--bugle-yellow)" : "var(--bugle-red)";

    document.getElementById("chip-report-count").textContent = data.reports.length;
    document.getElementById("chip-strike-count").textContent = user.strike_count;
    document.getElementById("chip-role").textContent = user.role;

    if (user.is_quarantined) {
      document.getElementById("quarantine-banner").style.display = "block";
    }

    renderReportHistory(data.reports);
  } catch (err) {
    console.error("Failed to load trust file:", err);
    showToast("Couldn't load your trust file.", "error");
  }
}

function renderReportHistory(reports) {
  const list = document.getElementById("report-history-list");
  if (!reports || reports.length === 0) {
    list.innerHTML = `<div class="empty-state"><p>You haven't filed any reports yet. Head to Submit Scoop to file your first.</p></div>`;
    return;
  }

  let badgeClass = (status) => {
    if (status === "VERIFIED") return "badge-verified";
    if (status === "COMMUNITY") return "badge-community";
    if (status === "BUSTED") return "badge-busted";
    return "badge-review";
  };

  list.innerHTML = reports.map((r) => `
    <a class="report-history-item" href="/incident/${r.incident_id}">
      <div>
        <div class="rh-title">${escapeHtmlProfile(r.title)}</div>
        <div class="rh-desc">${escapeHtmlProfile(r.description)}</div>
        <div class="rh-meta">${new Date(r.created_at).toLocaleString()} &bull; ${escapeHtmlProfile(r.category)}</div>
      </div>
      <span class="badge ${badgeClass(r.status)}">${r.status}</span>
    </a>
  `).join("");
}

function escapeHtmlProfile(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
