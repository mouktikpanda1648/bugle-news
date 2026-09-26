/* ==========================================================================
   THE DAILY BUGLE - EDITOR TRIAGE DESK CONTROLLER
   ========================================================================== */

async function loadDeskQueue() {
  const tbody = document.getElementById("desk-table-body");
  try {
    const response = await fetch("/api/incidents?view=desk");
    const incidents = await response.json();

    tbody.innerHTML = "";
    let verifiedCount = 0;
    let reviewCount = 0;

    if (incidents.length === 0) {
      tbody.innerHTML = `
        <tr><td colspan="6" style="text-align: center; padding: 2rem; color: var(--text-dim);">
          Queue is clear. No incidents currently awaiting inspection.
        </td></tr>`;
      updateStats(0, 0, 0);
      return;
    }

    incidents.forEach((inc) => {
      if (inc.status === "VERIFIED") verifiedCount++;
      if (inc.status === "REVIEW" || inc.status === "COMMUNITY") reviewCount++;

      let reasons = [];
      try { reasons = JSON.parse(inc.explainability_json); } catch (e) { reasons = []; }

      let scoreClass = "score-mid";
      if (inc.confidence_score >= 75) scoreClass = "score-high";
      else if (inc.confidence_score < 35) scoreClass = "score-low";

      let badgeClass = "badge-review";
      if (inc.status === "VERIFIED") badgeClass = "badge-verified";
      else if (inc.status === "COMMUNITY") badgeClass = "badge-community";
      else if (inc.status === "BUSTED") badgeClass = "badge-busted";

      const reasonsSummary = reasons.map((r) => `${r.label} (${r.value})`).join(" &bull; ");

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="incident-cell">
          <div class="cell-title">${escapeHtmlDesk(inc.title)}</div>
          <div class="cell-desc">${escapeHtmlDesk(inc.summary)}</div>
          <div style="font-size: 0.72rem; color: var(--text-dim); margin-top: 4px;">${reasonsSummary}</div>
        </td>
        <td><span style="font-weight: 700; color: var(--text-muted);">${escapeHtmlDesk(inc.category)}</span></td>
        <td><span class="score-badge ${scoreClass}">${inc.confidence_score}/100</span></td>
        <td>
          <div style="font-size: 0.85rem; font-weight: 600;">${inc.report_count} Reports</div>
          <div style="font-size: 0.75rem; color: var(--text-dim);">${inc.source_diversity} Unique Sources</div>
        </td>
        <td><span class="badge ${badgeClass}">${inc.status}</span></td>
        <td>
          <div class="action-cluster">
            <button class="btn-triage btn-triage-verify" onclick="executeTriage(${inc.id}, 'VERIFY')">Verify</button>
            <button class="btn-triage btn-triage-dispute" onclick="executeTriage(${inc.id}, 'DISPUTE')">Dispute & Strike</button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });

    updateStats(incidents.length, verifiedCount, reviewCount);
  } catch (err) {
    console.error("Failed to load triage queue:", err);
    showToast("Couldn't load the triage queue.", "error");
  }
}

function updateStats(total, verified, review) {
  document.getElementById("stat-total").textContent = total;
  document.getElementById("stat-verified").textContent = verified;
  document.getElementById("stat-review").textContent = review;
}

async function executeTriage(incidentId, action) {
  const formData = new FormData();
  formData.append("action", action);
  try {
    const res = await fetch(`/api/incidents/${incidentId}/action`, { method: "POST", body: formData });
    if (!res.ok) throw new Error("Action failed");
    showToast(action === "VERIFY" ? "Incident verified." : "Incident disputed — reporters struck.", "success", 2500);
    loadDeskQueue();
  } catch (err) {
    console.error("Action execution failed:", err);
    showToast("Error executing triage action.", "error");
  }
}

function escapeHtmlDesk(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
