(() => {
  const authInfo = document.getElementById("auth-info");
  const authConnect = document.getElementById("auth-connect");
  const authLogout = document.getElementById("auth-logout");
  const subreddit = document.getElementById("subreddit");
  const sort = document.getElementById("sort");
  const limit = document.getElementById("limit");
  const minScore = document.getElementById("min-score");
  const maxAge = document.getElementById("max-age");
  const keyword = document.getElementById("keyword");
  const tone = document.getElementById("tone");
  const textOnly = document.getElementById("text-only");
  const runBtn = document.getElementById("run");
  const btnLabel = runBtn.querySelector(".btn-label");
  const btnSpinner = runBtn.querySelector(".btn-spinner");
  const statusEl = document.getElementById("status");
  const queueEl = document.getElementById("queue");
  const clearDoneBtn = document.getElementById("clear-done");

  function setStatus(msg, kind = "") {
    statusEl.textContent = msg;
    statusEl.className = "status" + (kind ? ` ${kind}` : "");
  }

  function setLoading(on) {
    runBtn.disabled = on;
    btnSpinner.classList.toggle("hidden", !on);
    btnLabel.textContent = on ? "En cours…" : "Scanner & générer";
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function refreshAuth() {
    try {
      const res = await fetch("/api/reddit/auth/status");
      const data = await res.json();
      if (!data.configured) {
        authInfo.textContent = "Reddit non configuré — ajoute CLIENT_ID / SECRET dans .env";
        authConnect.classList.add("hidden");
        authLogout.classList.add("hidden");
        return;
      }
      if (data.connected) {
        authInfo.textContent = `Connecté : u/${data.username}`;
        authConnect.classList.add("hidden");
        authLogout.classList.remove("hidden");
      } else {
        authInfo.textContent = "Compte Reddit non connecté";
        authConnect.classList.remove("hidden");
        authLogout.classList.add("hidden");
      }
    } catch {
      authInfo.textContent = "Erreur auth";
    }
  }

  function renderQueue(items) {
    if (!items.length) {
      queueEl.innerHTML = '<p class="queue-empty">Aucun post en attente.</p>';
      return;
    }

    queueEl.innerHTML = "";
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "queue-card";
      if (item.status === "posted") card.classList.add("queue-posted");
      if (item.status === "skipped") card.classList.add("queue-skipped");

      const statusLabel =
        item.status === "posted"
          ? "Publié"
          : item.status === "skipped"
            ? "Ignoré"
            : "À valider";

      card.innerHTML = `
        <div class="queue-card-head">
          <span class="queue-status">${statusLabel}</span>
          <span class="queue-meta">${escapeHtml(item.subreddit)} · ▲ ${item.score}</span>
          <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener" class="queue-link">Voir le post</a>
        </div>
        <h3 class="queue-title">${escapeHtml(item.title)}</h3>
        ${item.body ? `<p class="queue-body">${escapeHtml(item.body.slice(0, 400))}${item.body.length > 400 ? "…" : ""}</p>` : ""}
        <label class="field-label">Réponse proposée</label>
        ${item.error ? `<p class="queue-error">${escapeHtml(item.error)}</p>` : ""}
        ${item.posted_url ? `<p class="queue-ok"><a href="${escapeHtml(item.posted_url)}" target="_blank" rel="noopener">Commentaire publié</a></p>` : ""}
        <div class="queue-actions"></div>
      `;

      const ta = document.createElement("textarea");
      ta.className = "textarea queue-reply";
      ta.rows = 4;
      ta.value = item.reply_text || "";
      if (item.status !== "pending") ta.readOnly = true;
      const actions = card.querySelector(".queue-actions");
      actions.before(ta);

      if (item.status === "pending") {
        const publishBtn = document.createElement("button");
        publishBtn.type = "button";
        publishBtn.className = "btn-main btn-sm";
        publishBtn.textContent = "Publier";
        publishBtn.addEventListener("click", async () => {
          await saveReply(item.id, ta.value);
          await publishItem(item.id, publishBtn);
        });

        const skipBtn = document.createElement("button");
        skipBtn.type = "button";
        skipBtn.className = "btn-ghost btn-sm";
        skipBtn.textContent = "Ignorer";
        skipBtn.addEventListener("click", () => skipItem(item.id));

        actions.appendChild(publishBtn);
        actions.appendChild(skipBtn);
      }

      queueEl.appendChild(card);
    });
  }

  async function saveReply(id, text) {
    await fetch(`/api/reddit/queue/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reply_text: text }),
    });
  }

  async function publishItem(id, btn) {
    btn.disabled = true;
    btn.textContent = "Publication…";
    try {
      const res = await fetch(`/api/reddit/queue/${id}/publish`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Erreur");
      setStatus("Commentaire publié.", "ok");
      await refreshQueue();
    } catch (err) {
      setStatus(err.message || "Échec publication.", "error");
      btn.disabled = false;
      btn.textContent = "Publier";
      await refreshQueue();
    }
  }

  async function skipItem(id) {
    await fetch(`/api/reddit/queue/${id}/skip`, { method: "POST" });
    await refreshQueue();
  }

  async function refreshQueue() {
    try {
      const res = await fetch("/api/reddit/queue");
      const items = await res.json();
      renderQueue(Array.isArray(items) ? items : []);
    } catch {
      queueEl.innerHTML = '<p class="queue-empty">Erreur chargement file.</p>';
    }
  }

  runBtn.addEventListener("click", async () => {
    const sub = subreddit.value.trim();
    if (!sub) {
      setStatus("Indique un subreddit.", "error");
      return;
    }

    setLoading(true);
    setStatus("Scan + génération en cours…");

    try {
      const res = await fetch("/api/reddit/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subreddit: sub,
          sort: sort.value,
          limit: Number(limit.value),
          min_score: Number(minScore.value) || 0,
          max_age_hours: Number(maxAge.value) || 48,
          keyword: keyword.value.trim(),
          text_only: textOnly.checked,
          tone: tone.value,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Erreur");

      setStatus(
        `${data.queued} post(s) en file (${data.scanned} scannés, ${data.skipped_duplicates} déjà présents).`,
        "ok",
      );
      await refreshQueue();
    } catch (err) {
      setStatus(err.message || "Échec.", "error");
    } finally {
      setLoading(false);
    }
  });

  authLogout.addEventListener("click", async () => {
    await fetch("/api/reddit/auth/logout", { method: "POST" });
    await refreshAuth();
    setStatus("Déconnecté.", "ok");
  });

  clearDoneBtn.addEventListener("click", async () => {
    await fetch("/api/reddit/queue", { method: "DELETE" });
    await refreshQueue();
  });

  const params = new URLSearchParams(location.search);
  if (params.get("auth_ok")) {
    setStatus("Compte Reddit connecté.", "ok");
    history.replaceState({}, "", "/auto");
  }
  if (params.get("auth_error")) {
    setStatus(`Connexion échouée : ${params.get("auth_error")}`, "error");
    history.replaceState({}, "", "/auto");
  }

  refreshAuth();
  refreshQueue();
})();
