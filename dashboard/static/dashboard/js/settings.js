/* Settings form: save POST, dirty state, toasts, skill search, TOC spy. */
(function () {
  const csrf = window.__CSRF__ || "";
  const form = document.getElementById("settings-form");
  if (!form) return;

  function toast(msg, type = "info", ms = 3200) {
    const host = document.getElementById("toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = msg;
    host.appendChild(el);
    while (host.children.length > 5) host.removeChild(host.firstChild);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 200);
    }, ms);
  }

  function setDirty(on) {
    const bar = document.querySelector(".save-bar");
    if (bar) bar.classList.toggle("dirty", !!on);
  }

  form.addEventListener("input", () => setDirty(true));
  form.addEventListener("change", () => setDirty(true));

  window.addEventListener("beforeunload", (e) => {
    if (document.querySelector(".save-bar.dirty")) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  function collect() {
    const o = {};
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) {
      if (form.elements[k] && form.elements[k].type === "checkbox") continue;
      o[k] = v;
    }
    ["headless", "show_browser", "dry_run", "ai_enabled", "karlancer_enabled",
     "forex_enabled", "kl_clamp_enabled"].forEach((n) => {
      const el = document.getElementById("f-" + n);
      if (el) o[n] = !!el.checked;
    });
    const boxes = form.querySelectorAll("#skill-cats input[type=checkbox]:checked");
    if (boxes.length) {
      o.skills = Array.from(boxes).map((b) => b.value);
    } else {
      const skills = document.getElementById("f-skills");
      o.skills = skills ? skills.value.split(",").map((s) => s.trim()).filter(Boolean) : [];
    }
    return o;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("save-msg");
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: {
          "X-CSRFToken": csrf,
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify(collect()),
      });
      const data = await res.json();
      if (data.ok) {
        setDirty(false);
        if (msg) {
          msg.textContent = data.message || "Saved";
          msg.style.color = "var(--ok)";
          setTimeout(() => { msg.textContent = ""; msg.style.color = ""; }, 4000);
        }
        toast(data.message || "ذخیره شد", "ok");
      } else {
        if (msg) {
          msg.textContent = data.error || "Error";
          msg.style.color = "var(--danger)";
        }
        toast(data.error || "خطا در ذخیره", "err");
      }
    } catch (err) {
      if (msg) msg.textContent = String(err);
      toast(String(err), "err");
    }
  });

  document.getElementById("btn-test-tg").onclick = async () => {
    toast("ارسال پیام تست تلگرام…", "info");
    const res = await fetch("/api/test-telegram", {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        token: document.getElementById("f-telegram_token").value,
        chat_id: document.getElementById("f-telegram_chat_id").value,
      }),
    });
    const data = await res.json();
    toast(data.ok ? (data.message || "Telegram در حال ارسال") : data.error,
      data.ok ? "ok" : "err");
  };

  document.getElementById("btn-test-ai").onclick = async () => {
    const out = document.getElementById("ai-test-result");
    if (out) out.textContent = "testing…";
    toast("در حال تست AI…", "info");
    await fetch("/api/test-ai", {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        api_key: document.getElementById("f-ai_api_key").value,
        model: document.getElementById("f-ai_model").value,
      }),
    });
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await (await fetch("/api/status", { credentials: "same-origin" })).json();
      if (!st.busy || !st.busy.test_ai) {
        if (out) out.textContent = "done — see log (Test AI …)";
        toast("تست AI تمام شد — خروجی در لاگ", "ok");
        break;
      }
    }
  };

  // skill search
  const skillSearch = document.getElementById("skill-search");
  if (skillSearch) {
    skillSearch.addEventListener("input", () => {
      const q = skillSearch.value.trim().toLowerCase();
      document.querySelectorAll("#skill-cats details").forEach((det) => {
        let any = false;
        det.querySelectorAll("label").forEach((lab) => {
          const hit = !q || lab.textContent.toLowerCase().includes(q);
          lab.style.display = hit ? "" : "none";
          if (hit) any = true;
        });
        det.style.display = any || !q ? "" : "none";
        if (q) det.open = true;
      });
    });
  }

  function updateSkillCount() {
    const badge = document.getElementById("skill-count");
    if (!badge) return;
    const n = form.querySelectorAll("#skill-cats input[type=checkbox]:checked").length;
    badge.textContent = String(n);
    badge.classList.toggle("hidden", n === 0);
  }
  form.addEventListener("change", (e) => {
    if (e.target && e.target.closest && e.target.closest("#skill-cats")) {
      updateSkillCount();
    }
  });

  // Open advanced collapse when deep-linking / TOC to pricing|forex
  function openAdvIfNeeded(hash) {
    const adv = document.getElementById("adv-more");
    if (!adv) return;
    if (hash === "#sec-pricing" || hash === "#sec-forex") {
      adv.open = true;
    }
  }

  function jumpTo(hash, smooth) {
    if (!hash || hash.charAt(0) !== "#") return;
    openAdvIfNeeded(hash);
    const el = document.querySelector(hash);
    if (!el) return;
    // Wait a frame so <details open> layout settles before scrolling
    requestAnimationFrame(() => {
      el.scrollIntoView({
        behavior: smooth === false ? "auto" : "smooth",
        block: "start",
      });
    });
  }

  openAdvIfNeeded(location.hash);
  if (location.hash && location.hash.length > 1) {
    jumpTo(location.hash, true);
  }
  window.addEventListener("hashchange", () => jumpTo(location.hash, true));
  document.querySelectorAll(".settings-toc a").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const href = a.getAttribute("href");
      history.replaceState(null, "", href);
      jumpTo(href, true);
      // keep spy in sync immediately
      document.querySelectorAll(".settings-toc a").forEach((x) => {
        x.classList.toggle("active", x === a);
      });
    });
  });

  // TOC scroll spy
  const tocLinks = document.querySelectorAll(".settings-toc a");
  const sections = document.querySelectorAll(".settings section[id]");
  if (tocLinks.length && sections.length && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const id = en.target.id;
        tocLinks.forEach((a) => {
          a.classList.toggle("active", a.getAttribute("href") === "#" + id);
        });
      });
    }, { rootMargin: "-20% 0px -60% 0px", threshold: 0 });
    sections.forEach((s) => io.observe(s));
  }

  // Load skill categories once
  (async function loadSkills() {
    const box = document.getElementById("skill-cats");
    if (!box) return;
    try {
      const data = await (await fetch("/api/skills", { credentials: "same-origin" })).json();
      const selected = new Set(
        (document.getElementById("f-skills").value || "")
          .split(",").map((s) => s.trim()).filter(Boolean)
      );
      const frag = document.createDocumentFragment();
      for (const cat of data.categories || []) {
        const det = document.createElement("details");
        const sum = document.createElement("summary");
        sum.textContent = cat.title || "…";
        det.appendChild(sum);
        for (const sk of cat.skills || []) {
          const lab = document.createElement("label");
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.value = sk;
          cb.checked = selected.has(sk);
          lab.appendChild(cb);
          lab.appendChild(document.createTextNode(sk));
          det.appendChild(lab);
        }
        frag.appendChild(det);
      }
      box.appendChild(frag);
    } catch (e) {
      box.textContent = "skill cache unavailable — use comma list";
    }
  })();
})();
