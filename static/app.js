(() => {
  let mode = "link";
  let lastReplies = [];
  let activeHistoryId = null;

  const tabs = document.querySelectorAll(".tab");
  const content = document.getElementById("content");
  const tone = document.getElementById("tone");
  const count = document.getElementById("count");
  const generateBtn = document.getElementById("generate");
  const btnLabel = generateBtn.querySelector(".btn-label");
  const btnSpinner = generateBtn.querySelector(".btn-spinner");
  const statusEl = document.getElementById("status");
  const preview = document.getElementById("preview");
  const previewSub = document.getElementById("preview-sub");
  const previewTitle = document.getElementById("preview-title");
  const previewBody = document.getElementById("preview-body");
  const results = document.getElementById("results");
  const repliesEl = document.getElementById("replies");
  const empty = document.getElementById("empty");
  const emptyPost = document.getElementById("empty-post");
  const copyAllBtn = document.getElementById("copy-all");
  const historyList = document.getElementById("history-list");
  const historyEmpty = document.getElementById("history-empty");
  const historyClear = document.getElementById("history-clear");

  const placeholders = {
    link: "https://www.reddit.com/r/.../comments/...",
    text: "Titre du post (optionnel)\n\nColle le texte ici…",
  };

  const modeContent = { link: "", text: "" };

  function switchMode(newMode) {
    if (newMode === mode) return;
    modeContent[mode] = content.value;
    mode = newMode;
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    content.value = modeContent[mode];
    content.placeholder = placeholders[mode];
    content.focus();
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => switchMode(tab.dataset.mode));
  });

  content.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      generateBtn.click();
    }
  });

  function apiError(data, fallback = "Erreur serveur") {
    const d = data?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x.msg || x).join(" ");
    return fallback;
  }

  function setStatus(msg, kind = "") {
    statusEl.textContent = msg;
    statusEl.className = "status" + (kind ? ` ${kind}` : "");
  }

  function setLoading(on) {
    generateBtn.disabled = on;
    btnSpinner.classList.toggle("hidden", !on);
    btnLabel.textContent = on ? "Génération…" : "Générer";
  }

  function formatDate(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleString("fr-FR", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "Copié ✓";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = prev;
        btn.classList.remove("copied");
      }, 1600);
    }
  }

  function showPreview(data) {
    previewSub.textContent = data.subreddit || "r/...";
    const title = (data.title || "").trim();
    const body = (data.body || "").trim();
    const same = !body || body === title;

    if (same) {
      previewTitle.textContent = title || body || "Post";
      previewBody.textContent = "";
      previewBody.classList.add("hidden");
    } else {
      previewTitle.textContent = title;
      previewBody.textContent = body;
      previewBody.classList.remove("hidden");
    }
    preview.classList.remove("hidden");
    emptyPost.classList.add("hidden");
  }

  function renderReplies(replies) {
    lastReplies = replies;
    repliesEl.innerHTML = "";
    replies.forEach((text, i) => {
      const card = document.createElement("article");
      card.className = "reply-card";
      card.style.animationDelay = `${i * 40}ms`;

      const head = document.createElement("div");
      head.className = "reply-head";

      const num = document.createElement("span");
      num.className = "reply-num";
      num.textContent = `Réponse ${i + 1}`;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-copy";
      btn.textContent = "Copier";
      btn.addEventListener("click", () => copyText(text, btn));

      head.appendChild(num);
      head.appendChild(btn);

      const p = document.createElement("p");
      p.className = "reply-text";
      p.textContent = text;

      card.appendChild(head);
      card.appendChild(p);
      repliesEl.appendChild(card);
    });

    empty.classList.add("hidden");
    results.classList.remove("hidden");
  }

  function setActiveHistory(id) {
    activeHistoryId = id;
    historyList.querySelectorAll(".history-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.id === id);
    });
  }

  async function loadHistoryItem(id) {
    try {
      const res = await fetch(`/api/history/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Introuvable");

      mode = data.mode || "text";
      modeContent[mode] = data.content || "";
      tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
      content.placeholder = placeholders[mode];
      content.value = modeContent[mode];

      if (data.tone) {
        tone.value = data.tone;
      }
      if (data.count) {
        count.value = String(data.count);
      }

      showPreview(data);
      renderReplies(data.replies || []);
      setActiveHistory(id);
      setStatus(`${(data.replies || []).length} réponses (historique)`, "ok");
    } catch (err) {
      setStatus(err.message || "Erreur historique.", "error");
    }
  }

  function renderHistoryList(items) {
    historyList.innerHTML = "";
    const has = items && items.length > 0;
    historyEmpty.classList.toggle("hidden", has);
    historyList.classList.toggle("hidden", !has);
    historyClear.classList.toggle("hidden", !has);

    if (!has) return;

    items.forEach((item) => {
      const li = document.createElement("li");
      li.className = "history-item";
      li.dataset.id = item.id;
      if (item.id === activeHistoryId) li.classList.add("active");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-btn";
      btn.innerHTML = `
        <span class="history-date">${formatDate(item.created_at)}</span>
        <span class="history-item-title">${escapeHtml(item.title || "Sans titre")}</span>
        <span class="history-item-meta">${escapeHtml(item.subreddit || "r/...")} · ${item.reply_count} rép.</span>
      `;
      btn.addEventListener("click", () => loadHistoryItem(item.id));

      const del = document.createElement("button");
      del.type = "button";
      del.className = "history-del";
      del.title = "Supprimer";
      del.textContent = "×";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        await deleteHistoryItem(item.id);
      });

      li.appendChild(btn);
      li.appendChild(del);
      historyList.appendChild(li);
    });
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function refreshHistory() {
    try {
      const res = await fetch("/api/history");
      const items = await res.json();
      renderHistoryList(Array.isArray(items) ? items : []);
    } catch {
      /* ignore */
    }
  }

  async function deleteHistoryItem(id) {
    try {
      await fetch(`/api/history/${id}`, { method: "DELETE" });
      if (activeHistoryId === id) activeHistoryId = null;
      await refreshHistory();
    } catch {
      setStatus("Impossible de supprimer.", "error");
    }
  }

  historyClear.addEventListener("click", async () => {
    if (!confirm("Effacer tout l'historique ?")) return;
    try {
      await fetch("/api/history", { method: "DELETE" });
      activeHistoryId = null;
      await refreshHistory();
      setStatus("Historique effacé.", "ok");
    } catch {
      setStatus("Erreur.", "error");
    }
  });

  copyAllBtn.addEventListener("click", () => {
    if (!lastReplies.length) return;
    const blob = lastReplies.map((r, i) => `--- ${i + 1} ---\n${r}`).join("\n\n");
    copyText(blob, copyAllBtn);
  });

  generateBtn.addEventListener("click", async () => {
    const body = content.value.trim();
    if (body.length < 3) {
      setStatus("Ajoute un lien ou du texte.", "error");
      content.focus();
      return;
    }

    setLoading(true);
    const t0 = performance.now();

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          mode,
          content: body,
          tone: tone.value,
          count: Number(count.value),
        }),
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(apiError(data));
      }

      showPreview(data);
      renderReplies(data.replies);

      if (data.history_id) {
        setActiveHistory(data.history_id);
      }
      await refreshHistory();

      const sec = data.elapsed_ms
        ? (data.elapsed_ms / 1000).toFixed(1)
        : ((performance.now() - t0) / 1000).toFixed(1);
      let msg = `${data.replies.length} réponses · ${sec}s`;
      if (mode === "link" && !(data.body || "").trim()) {
        msg += " · corps non lu (titre seul)";
      }
      setStatus(msg, "ok");
    } catch (err) {
      if (err.name === "AbortError") {
        setStatus("Trop long — réduis le nombre de réponses.", "error");
      } else {
        setStatus(err.message || "Échec.", "error");
      }
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  });

  refreshHistory();
})();
