/* Shared header session sync. Each page calls initHeaderSession(opts). */

async function initHeaderSession({ requireAuth = false, requireEditor = false, loginRedirect = "/" } = {}) {
  const container = document.getElementById("auth-header-container");
  const deskNav = document.getElementById("desk-nav-link");

  try {
    const res = await fetch("/api/auth/me");
    const session = await res.json();

    if (!session.authenticated) {
      if (deskNav) deskNav.style.display = "none";
      if (container) {
        container.innerHTML = `<a href="/login?redirect=${encodeURIComponent(loginRedirect)}" class="btn-auth-action">Sign In</a>`;
      }
      if (requireAuth) {
        window.location.href = `/login?redirect=${encodeURIComponent(loginRedirect)}`;
      }
      return session;
    }

    if (requireEditor && session.role !== "EDITOR") {
      window.location.href = "/login?redirect=" + encodeURIComponent(loginRedirect);
      return session;
    }

    const roleClass = session.role === "EDITOR" ? "role-editor" : "role-citizen";
    if (session.role === "EDITOR" && deskNav) deskNav.style.display = "inline-block";

    if (container) {
      container.innerHTML = `
        <div class="user-profile-badge">
          <a href="/profile" class="user-name-link">${escapeHtml(session.name || session.username)}</a>
          <span class="user-role-tag ${roleClass}">${session.role}</span>
          <button class="btn-icon-action" onclick="signOutUser()" title="Sign out">&#8594;</button>
        </div>
      `;
    }
    return session;
  } catch (err) {
    console.error("Session sync failed:", err);
    if (requireAuth) window.location.href = `/login?redirect=${encodeURIComponent(loginRedirect)}`;
    return { authenticated: false };
  }
}

async function signOutUser() {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
