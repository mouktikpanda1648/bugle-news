/* ==========================================================================
   THE DAILY BUGLE - CITIZEN REPORT FORM & SOS CONTROLLER
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  const reportForm = document.getElementById("incident-report-form");
  const latInput = document.getElementById("form-lat");
  const lngInput = document.getElementById("form-lng");
  const fileInput = document.getElementById("file-upload");
  const filenameDisplay = document.getElementById("file-selected-name");
  const categorySelect = document.getElementById("category-select");
  const submitBtn = document.getElementById("submit-btn");
  const geoStatus = document.getElementById("geo-status");

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        latInput.value = position.coords.latitude.toFixed(6);
        lngInput.value = position.coords.longitude.toFixed(6);
        if (geoStatus) geoStatus.textContent = `GPS locked: ${latInput.value}, ${lngInput.value}`;
      },
      (error) => {
        console.warn("Geolocation permission denied. Defaulting to municipal center.", error);
        if (geoStatus) geoStatus.textContent = "Location unavailable — using approximate municipal center.";
      },
      { timeout: 8000, enableHighAccuracy: true }
    );
  } else if (geoStatus) {
    geoStatus.textContent = "Geolocation not supported on this device.";
  }

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) {
        filenameDisplay.textContent = `Attached: ${file.name} (${Math.round(file.size / 1024)} KB)`;
        filenameDisplay.style.display = "block";
      } else {
        filenameDisplay.style.display = "none";
      }
    });
  }

  if (categorySelect) {
    categorySelect.addEventListener("change", (e) => {
      const val = e.target.value;
      const policeBtn = document.getElementById("sos-police");
      const fireBtn = document.getElementById("sos-fire");
      const medicalBtn = document.getElementById("sos-ambulance");

      [policeBtn, fireBtn, medicalBtn].forEach((btn) => { if (btn) btn.style.boxShadow = "none"; });

      if (val === "Assault" && policeBtn) {
        policeBtn.style.boxShadow = "0 0 12px 3px rgba(239, 35, 89, 0.9)";
      } else if (val === "Fire" && fireBtn) {
        fireBtn.style.boxShadow = "0 0 12px 3px rgba(242, 169, 60, 0.9)";
      } else if (val === "Obstruction" && medicalBtn) {
        medicalBtn.style.boxShadow = "0 0 12px 3px rgba(34, 196, 141, 0.9)";
      }
    });
  }

  if (reportForm) {
    reportForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      submitBtn.disabled = true;
      submitBtn.textContent = "Transmitting to Trust Cascade...";

      const formData = new FormData(reportForm);

      try {
        const response = await fetch("/api/reports", { method: "POST", body: formData });
        const result = await response.json();

        if (!response.ok) {
          showToast(result.error || "Could not submit your report.", "error");
          return;
        }

        if (result.status === "quarantined") {
          showToast("Account under quarantine audit: your report has been routed to human review.", "warning", 6000);
        } else {
          showToast(`Report accepted! Confidence score computed: ${result.score || 50}/100`, "success", 3000);
        }

        setTimeout(() => { window.location.href = "/"; }, 900);
      } catch (err) {
        console.error("Submission failed:", err);
        showToast("Transmission error: unable to reach the Trust Engine.", "error");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Transmit to Trust Cascade";
      }
    });
  }
});
