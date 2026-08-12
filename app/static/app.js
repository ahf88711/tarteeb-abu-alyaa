/* ترتيب أبو علياء — Arabic RTL frontend */
(() => {
  "use strict";

  let sessionId = null;
  let datesPayload = [];
  let lastResults = [];

  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");

  function setStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.classList.toggle("error", !!isError);
  }

  function setStep(n) {
    document.querySelectorAll(".step-pill").forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
  }

  function badge(status) {
    if (!status) return "";
    if (status.includes("مؤكد") || status === "مرتّب") return `<span class="badge badge-ok">${status}</span>`;
    if (status.includes("تعادل غير") || status.includes("غير محسوم") || status.includes("غير موجود"))
      return `<span class="badge badge-danger">${status}</span>`;
    if (status.includes("تعادل") || status.includes("مراجعة") || status.includes("محسوم"))
      return `<span class="badge badge-warn">${status}</span>`;
    return `<span class="badge badge-info">${status}</span>`;
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    let data;
    try {
      data = await res.json();
    } catch {
      data = {};
    }
    if (!res.ok) {
      const detail = data.detail || res.statusText || "خطأ غير معروف";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const data = await api("/api/session", { method: "POST" });
    sessionId = data.session_id;
    // folder import can run without a prior master upload
    if ($("btnFolder")) $("btnFolder").disabled = false;
    return sessionId;
  }

  // allow typing folder path before anything else
  document.addEventListener("DOMContentLoaded", () => {
    const inp = $("masterFolder");
    if (inp) {
      inp.addEventListener("focus", () => {
        ensureSession().catch(() => {});
      });
    }
  });

  // Drag & drop files
  const dz = $("dropZone");
  if (dz) {
    ["dragenter", "dragover"].forEach((evName) => {
      dz.addEventListener(evName, (e) => {
        e.preventDefault();
        dz.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((evName) => {
      dz.addEventListener(evName, (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
      });
    });
    dz.addEventListener("drop", async (e) => {
      const files = [...(e.dataTransfer?.files || [])];
      if (!files.length) return;
      try {
        await ensureSession();
        const masters = [];
        const targets = [];
        for (const f of files) {
          const n = (f.name || "").toLowerCase();
          if (
            n.endsWith(".pdf") ||
            n.endsWith(".xlsx") ||
            n.endsWith(".xlsm") ||
            /سراء|master|رئيسي/.test(n)
          ) {
            masters.push(f);
          } else {
            targets.push(f);
          }
        }
        // Heuristic: if only images and no master yet, treat as targets unless user has no master
        setStatus(`استلام ${files.length} ملف(ات)…`);
        if (masters.length) {
          const fd = new FormData();
          masters.forEach((f) => fd.append("files", f));
          const data = await api(`/api/upload/master/multi?session_id=${sessionId}`, {
            method: "POST",
            body: fd,
          });
          $("masterInfo").textContent = `تم: ${data.master_people_count} شخص (دمج ${data.files_merged || masters.length}).`;
          $("btnTargets").disabled = false;
          $("btnManual").disabled = false;
          $("btnFolder").disabled = false;
          renderMasterPreview(data.people || []);
          wireExports();
        }
        if (targets.length) {
          const fd = new FormData();
          if (targets.length === 1) {
            fd.append("file", targets[0]);
            const data = await api(`/api/upload/targets?session_id=${sessionId}`, {
              method: "POST",
              body: fd,
            });
            renderNames(data.target_names || []);
            $("targetInfo").textContent = `استُخرج ${data.target_names.length} اسمًا.`;
          } else {
            targets.forEach((f) => fd.append("files", f));
            const data = await api(`/api/upload/targets/multi?session_id=${sessionId}`, {
              method: "POST",
              body: fd,
            });
            renderNames(data.target_names || []);
            $("targetInfo").textContent = `استُخرج ${data.target_names.length} اسمًا.`;
          }
          $("panel-names").classList.remove("hidden");
          setStep(2);
        }
        setStatus("تم استلام الملفات المسحوبة. راجع الأسماء عند الحاجة.");
      } catch (err) {
        setStatus(err.message || String(err), true);
      }
    });
  }

  if ($("btnRerank")) {
    $("btnRerank").onclick = withLoading($("btnRerank"), async () => {
      await ensureSession();
      setStatus("إعادة تنفيذ الترتيب الحتمي…");
      const data = await api("/api/rank", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, auto_verify_dates: false }),
      });
      renderResults(data.results || [], data.summary || {});
      wireExports();
      setStatus(data.messages?.slice(-1)[0] || "أُعيد الترتيب.");
    });
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", (ev) => {
    if (ev.target && ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const k = ev.key;
    if (k === "g" || k === "G" || k === "ق") {
      // full rank demo
      $("btnFullRank")?.click();
    } else if (k === "n" || k === "N" || k === "ا") {
      $("btnReset")?.click();
    } else if (k === "?" || k === "h" || k === "H") {
      setStatus(
        "اختصارات: G تشغيل كامل · N جلسة جديدة · 1–4 خطوات · ? مساعدة"
      );
    } else if (k >= "1" && k <= "4") {
      setStep(Number(k));
      const map = { 1: "panel-upload", 2: "panel-names", 3: "panel-dates", 4: "panel-results" };
      const el = $(map[k]);
      if (el && !el.classList.contains("hidden")) {
        el.scrollIntoView({ behavior: "smooth" });
      }
    }
  });

  function withLoading(btn, fn) {
    return async (...args) => {
      if (!btn) return fn(...args);
      const old = btn.innerHTML;
      const isAnchor = btn.tagName === "A";
      if (!isAnchor) btn.disabled = true;
      btn.classList.add("is-loading");
      btn.innerHTML = `<span class="loader"></span> جاري…`;
      try {
        await fn(...args);
      } catch (e) {
        setStatus(e.message || String(e), true);
      } finally {
        if (!isAnchor) btn.disabled = false;
        btn.classList.remove("is-loading");
        btn.innerHTML = old;
      }
    };
  }

  async function renameMaster(oldKey, current) {
    const neu = window.prompt("الاسم الصحيح:", current);
    if (!neu || !neu.trim() || neu.trim() === current) return;
    try {
      await ensureSession();
      const data = await api("/api/master/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          old_key: oldKey,
          new_name: neu.trim(),
        }),
      });
      renderMasterPreview(data.people || []);
      setStatus(data.messages?.slice(-1)[0] || "تم تصحيح الاسم.");
    } catch (e) {
      setStatus(e.message || String(e), true);
    }
  }

  function renderMasterPreview(people) {
    const box = $("masterPreview");
    const body = $("masterBody");
    const cards = $("masterCards");
    if (!people || !people.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");

    if (body) {
      body.innerHTML = people
        .map(
          (p) => `
      <tr data-key="${escapeHtml(p.normalized || p.name)}">
        <td>
          <strong class="master-name">${escapeHtml(p.name)}</strong>
          <div class="btn-row" style="margin-top:0.25rem">
            <button type="button" class="btn btn-outline btn-rename"
              data-key="${escapeHtml(p.normalized || p.name)}" data-name="${escapeHtml(p.name)}">تصحيح</button>
          </div>
        </td>
        <td>${escapeHtml(p.rank_title || "—")}</td>
        <td>${(p.pages || []).join("، ") || "—"}</td>
        <td>${p.date_count ?? 0}${
          p.needs_review_dates ? ` <span class="badge badge-warn">${p.needs_review_dates} مراجعة</span>` : ""
        }</td>
        <td class="muted">${(p.dates || []).slice(0, 4).map(escapeHtml).join(" · ") || "—"}</td>
      </tr>`
        )
        .join("");
      body.querySelectorAll(".btn-rename").forEach((btn) => {
        btn.onclick = () => renameMaster(btn.dataset.key, btn.dataset.name || "");
      });
    }

    if (cards) {
      cards.innerHTML = people
        .map(
          (p) => `
        <article class="master-card" data-name="${escapeHtml(p.name)}">
          <div class="card-title master-name">${escapeHtml(p.name)}</div>
          <div class="meta-row">
            <span>${escapeHtml(p.rank_title || "—")}</span>
            <span>ص: ${(p.pages || []).join("، ") || "—"}</span>
            <span>تواريخ: ${p.date_count ?? 0}</span>
          </div>
          <div class="muted">${(p.dates || []).slice(0, 3).map(escapeHtml).join(" · ") || "—"}</div>
          <div class="card-actions">
            <button type="button" class="btn btn-outline btn-rename"
              data-key="${escapeHtml(p.normalized || p.name)}" data-name="${escapeHtml(p.name)}">تصحيح الاسم</button>
          </div>
        </article>`
        )
        .join("");
      cards.querySelectorAll(".btn-rename").forEach((btn) => {
        btn.onclick = () => renameMaster(btn.dataset.key, btn.dataset.name || "");
      });
    }

    const mf = $("masterFilter");
    if (mf) {
      mf.oninput = () => {
        const q = mf.value.trim();
        if (body) {
          body.querySelectorAll("tr").forEach((tr) => {
            const name = tr.querySelector(".master-name")?.textContent || "";
            tr.style.display = !q || name.includes(q) ? "" : "none";
          });
        }
        if (cards) {
          cards.querySelectorAll(".master-card").forEach((card) => {
            const name = card.dataset.name || "";
            card.style.display = !q || name.includes(q) ? "" : "none";
          });
        }
      };
    }
  }

  $("btnMaster").onclick = withLoading($("btnMaster"), async () => {
    await ensureSession();
    const files = [...($("masterFile").files || [])];
    if (!files.length) throw new Error("اختر ملف PDF أو Excel رئيسيًا أولًا.");
    setStatus(
      files.length > 1
        ? `جاري دمج ومعالجة ${files.length} ملفات رئيسية…`
        : "جاري معالجة الملف الرئيسي بالكامل… قد يستغرق وقتًا للصفحات الممسوحة."
    );
    let data;
    if (files.length === 1) {
      const fd = new FormData();
      fd.append("file", files[0]);
      data = await api(`/api/upload/master?session_id=${sessionId}`, {
        method: "POST",
        body: fd,
      });
    } else {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      data = await api(`/api/upload/master/multi?session_id=${sessionId}`, {
        method: "POST",
        body: fd,
      });
    }
    const merged = data.files_merged ? ` (دُمج ${data.files_merged} ملفات)` : "";
    $("masterInfo").textContent = `تم: ${data.master_people_count} شخص في الفهرس${merged}.`;
    $("btnTargets").disabled = false;
    $("btnManual").disabled = false;
    $("btnFolder").disabled = false;
    renderMasterPreview(data.people || []);
    if ($("btnMasterXlsx")) {
      $("btnMasterXlsx").href = `/api/export/master?session_id=${sessionId}`;
    }
    setStatus(data.messages?.slice(-1)[0] || "تم تحميل الملف الرئيسي.");
  });

  $("btnFolder").onclick = withLoading($("btnFolder"), async () => {
    await ensureSession();
    const folder = ($("masterFolder").value || "").trim();
    if (!folder) throw new Error("أدخل مسار المجلد الكامل.");
    setStatus("جاري دمج كل ملفات المجلد (PDF/صور/Excel)…");
    const data = await api("/api/upload/master/folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, folder_path: folder }),
    });
    $("masterInfo").textContent = `تم دمج ${data.files_merged} ملفًا — ${data.master_people_count} شخص.`;
    $("btnTargets").disabled = false;
    $("btnManual").disabled = false;
    renderMasterPreview(data.people || []);
    if ($("btnMasterXlsx")) {
      $("btnMasterXlsx").href = `/api/export/master?session_id=${sessionId}`;
    }
    setStatus(data.messages?.slice(-1)[0] || "تم دمج المجلد.");
  });

  async function loadDemo(mode) {
    setStatus(
      mode === "excel"
        ? "جاري تحميل عينة Excel النظيفة + قائمة الأسماء…"
        : "جاري تحميل عينة PDF الممسوحة + قائمة الأسماء…"
    );
    const data = await api(`/api/demo/samples?mode=${encodeURIComponent(mode)}`, {
      method: "POST",
    });
    sessionId = data.session_id;
    $("masterInfo").textContent = `عينة: ${data.master_people_count} شخص في الفهرس.`;
    $("btnTargets").disabled = false;
    $("btnManual").disabled = false;
    $("btnFolder").disabled = false;
    renderMasterPreview(data.people || []);
    if (data.target_names && data.target_names.length) {
      $("targetInfo").textContent = `استُخرج من العينة: ${data.target_names.length} اسمًا.`;
      renderNames(data.target_names);
      $("panel-names").classList.remove("hidden");
      setStep(2);
    }
    setStatus(
      (data.note || "") +
        " " +
        (data.messages?.slice(-1)[0] || "راجِع الأسماء ثم اعتمدها.")
    );
  }

  $("btnDemo").onclick = withLoading($("btnDemo"), async () => loadDemo("pdf"));
  $("btnDemoExcel").onclick = withLoading($("btnDemoExcel"), async () => loadDemo("excel"));

  $("btnFullRank").onclick = withLoading($("btnFullRank"), async () => {
    setStatus("تشغيل كامل: ملف رئيسي Excel + قائمة مطلوبين → ترتيب حتمي…");
    const data = await api("/api/demo/full_rank", { method: "POST" });
    sessionId = data.session_id;
    $("masterInfo").textContent = `عينة كاملة: ${data.master_people_count} شخص.`;
    $("btnTargets").disabled = false;
    $("btnManual").disabled = false;
    $("btnFolder").disabled = false;
    renderMasterPreview(data.people || []);
    if (data.target_names) {
      $("targetInfo").textContent = `مطلوبون: ${data.target_names.length}`;
      renderNames(data.target_names);
      $("panel-names").classList.remove("hidden");
    }
    renderResults(data.results || [], data.summary || {});
    $("panel-dates").classList.remove("hidden");
    $("panel-results").classList.remove("hidden");
    setStep(4);
    wireExports();
    setStatus(
      (data.note || "") +
        " " +
        (data.messages?.slice(-1)[0] || "اكتمل الترتيب.")
    );
    $("panel-results").scrollIntoView({ behavior: "smooth" });
  });

  $("btnManual").onclick = withLoading($("btnManual"), async () => {
    await ensureSession();
    const text = $("manualNames").value || "";
    const names = text
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!names.length) throw new Error("أدخل اسمًا واحدًا على الأقل (سطر لكل اسم).");
    setStatus("جاري مطابقة الأسماء اليدوية مع الملف الرئيسي…");
    const data = await api("/api/names/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, names }),
    });
    $("targetInfo").textContent = `الإجمالي الآن: ${data.target_names.length} اسمًا.`;
    renderNames(data.target_names);
    $("panel-names").classList.remove("hidden");
    setStep(2);
    setStatus(data.messages?.slice(-1)[0] || "تمت إضافة الأسماء. راجعها قبل الاعتماد.");
  });

  $("btnTargets").onclick = withLoading($("btnTargets"), async () => {
    await ensureSession();
    const files = [...($("targetFile").files || [])];
    if (!files.length) throw new Error("اختر صورة أو ملف قائمة الأسماء.");
    setStatus(
      files.length > 1
        ? `جاري استخراج ودمج ${files.length} قوائم أسماء…`
        : "جاري استخراج الأسماء والتحقق منها مقابل الملف الرئيسي…"
    );
    let data;
    if (files.length === 1) {
      const fd = new FormData();
      fd.append("file", files[0]);
      data = await api(`/api/upload/targets?session_id=${sessionId}`, {
        method: "POST",
        body: fd,
      });
    } else {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      data = await api(`/api/upload/targets/multi?session_id=${sessionId}`, {
        method: "POST",
        body: fd,
      });
    }
    $("targetInfo").textContent = `استُخرج ${data.target_names.length} اسمًا.`;
    renderNames(data.target_names);
    $("panel-names").classList.remove("hidden");
    setStep(2);
    setStatus(data.messages?.slice(-1)[0] || "راجع الأسماء المستخرجة.");
  });

  function renderNames(names) {
    const box = $("namesList");
    if (!names.length) {
      box.innerHTML = `<p class="hint">لم يُستخرج أي اسم. جرّب صورة أوضح أو أدخل الأسماء يدويًا لاحقًا.</p>`;
      return;
    }
    box.innerHTML = names
      .map((t) => {
        const needs =
          t.status.includes("مراجعة") ||
          t.status.includes("محسوم") ||
          t.status.includes("غير موجود");
        const amb = t.status.includes("محسوم");
        const candOpts = (t.candidates || [])
          .map(
            (c) =>
              `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} (${Math.round(
                c.confidence * 100
              )}%)</option>`
          )
          .join("");
        return `
        <div class="review-card ${amb ? "ambiguous" : needs ? "needs" : ""}" data-id="${t.id}">
          ${t.crop_path ? `<img src="${escapeHtml(t.crop_path)}" alt="قصاصة الاسم من المصدر" loading="lazy" style="max-width:100%;max-height:110px;border:1px solid var(--border);border-radius:8px;margin-bottom:.5rem" />` : ""}
          <div><strong>${escapeHtml(t.original_name)}</strong> ${badge(t.status)}</div>
          <div class="muted">OCR: ${escapeHtml(t.ocr_raw || t.original_name)} · الثقة: ${Math.round(
          (t.confidence || 0) * 100
        )}%</div>
          ${
            t.matched_master_name
              ? `<div class="muted">مطابقة مقترحة من الملف الرئيسي: <strong>${escapeHtml(
                  t.matched_master_name
                )}</strong></div>`
              : ""
          }
          ${
            amb
              ? `<div class="hint" style="color:var(--danger)">الاسم غير محسوم ويحتاج مراجعة — لا تخمين تلقائي.</div>`
              : ""
          }
          <label class="muted">تصحيح/اختيار الاسم
            <input type="text" class="name-input" value="${escapeHtml(
              t.matched_master_name || t.original_name
            )}" />
          </label>
          ${
            candOpts
              ? `<label class="muted">مرشحو الملف الرئيسي
                  <select class="cand-select">
                    <option value="">— اختر —</option>
                    ${candOpts}
                  </select>
                </label>`
              : ""
          }
          <div class="btn-row">
            <label class="muted"><input type="checkbox" class="confirm-cb" ${
              t.status === "مؤكد"
                ? "checked"
                : ""
            } /> تأكيد هذا الاسم للترتيب</label>
          </div>
        </div>`;
      })
      .join("");

    // Quick actions
    const bar = document.createElement("div");
    bar.className = "btn-row";
    bar.innerHTML = `
      <button type="button" class="btn btn-outline" id="btnSelectSuggested">تحديد كل المقترحات القوية</button>
      <button type="button" class="btn btn-outline" id="btnSelectNone">إلغاء تحديد الكل</button>
    `;
    box.prepend(bar);
    bar.querySelector("#btnSelectSuggested").onclick = () => {
      box.querySelectorAll(".review-card").forEach((card) => {
        const confText = card.querySelector(".muted")?.textContent || "";
        const hasMatch = (card.querySelector(".name-input")?.value || "").trim().length > 0;
        const m = confText.match(/الثقة:\s*(\d+)/);
        const conf = m ? Number(m[1]) : 0;
        card.querySelector(".confirm-cb").checked = hasMatch && conf >= 97;
      });
    };
    bar.querySelector("#btnSelectNone").onclick = () => {
      box.querySelectorAll(".confirm-cb").forEach((cb) => (cb.checked = false));
    };

    box.querySelectorAll(".cand-select").forEach((sel) => {
      sel.addEventListener("change", () => {
        if (!sel.value) return;
        const card = sel.closest(".review-card");
        card.querySelector(".name-input").value = sel.value;
        card.querySelector(".confirm-cb").checked = true;
      });
    });
  }

  if ($("btnAutoConfirm")) {
    $("btnAutoConfirm").onclick = withLoading($("btnAutoConfirm"), async () => {
      await ensureSession();
      setStatus("تأكيد حذر للأسماء عالية الثقة فقط…");
      const data = await api("/api/names/auto_confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, min_confidence: 0.97 }),
      });
      renderNames(data.target_names || []);
      setStatus(data.messages?.slice(-1)[0] || "تم التأكيد الحذر.");
    });
  }

  $("btnConfirmNames").onclick = withLoading($("btnConfirmNames"), async () => {
    const cards = [...document.querySelectorAll("#namesList .review-card")];
    const corrections = cards.map((card) => {
      const id = card.dataset.id;
      const name = card.querySelector(".name-input").value.trim();
      const confirmed = card.querySelector(".confirm-cb").checked;
      if (confirmed) {
        return { id, action: "set_name", name };
      }
      return { id, action: "reject" };
    });
    setStatus("جاري اعتماد الأسماء وجمع التواريخ…");
    const data = await api("/api/names/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, corrections }),
    });
    datesPayload = data.dates_for_review || [];
    renderDates(datesPayload);
    $("panel-dates").classList.remove("hidden");
    setStep(3);
    setStatus(`جاهز لمراجعة التواريخ (${datesPayload.length} شخص مؤكد).`);
  });

  function renderDates(items) {
    const box = $("datesList");
    if (!items.length) {
      box.innerHTML = `<p class="hint">لا أشخاص مؤكدين لديهم سجلات في الملف الرئيسي.</p>`;
      return;
    }
    box.innerHTML = items
      .map((p) => {
        const dates = (p.dates || [])
          .map(
            (d) => `
            <label class="muted" style="display:block;margin:0.2rem 0">
              <input type="checkbox" class="date-cb" data-key="${escapeHtml(
                p.master_key
              )}" data-date="${escapeHtml(d.normalized_date)}" checked />
              ${escapeHtml(d.display || d.normalized_date)}
              <span class="muted">(ص ${d.page} · ثقة ${Math.round((d.confidence || 0) * 100)}%)</span>
              <span class="muted">${escapeHtml(d.original_text || "")}</span>
            </label>`
          )
          .join("");
        return `
        <div class="review-card" data-key="${escapeHtml(p.master_key)}">
          <div><strong>${escapeHtml(p.name)}</strong>
            <span class="muted">الصفحات: ${(p.pages || []).join("، ") || "—"}</span>
          </div>
          <div class="muted">أزل التحديد عن أي تاريخ غير صحيح قبل الترتيب.</div>
          ${dates || '<div class="muted">لا تواريخ مستخرجة</div>'}
          <label class="muted">إضافة تاريخ (مثال 1447/08/15)
            <input type="text" class="add-date" placeholder="YYYY/MM/DD" />
          </label>
        </div>`;
      })
      .join("");
  }

  $("btnBulkSafe").onclick = withLoading($("btnBulkSafe"), async () => {
    await ensureSession();
    const data = await api("/api/dates/bulk_verify_safe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    setStatus(data.messages?.slice(-1)[0] || "تم اعتماد التواريخ الآمنة.");
  });

  $("btnRank").onclick = withLoading($("btnRank"), async () => {
    // Build date reviews from checkboxes
    const reviews = [];
    document.querySelectorAll("#datesList .review-card").forEach((card) => {
      const key = card.dataset.key;
      const checked = new Set(
        [...card.querySelectorAll(".date-cb:checked")].map((cb) => cb.dataset.date)
      );
      const all = [...card.querySelectorAll(".date-cb")];
      all.forEach((cb) => {
        if (!checked.has(cb.dataset.date)) {
          reviews.push({
            master_key: key,
            action: "delete_date",
            date: cb.dataset.date,
          });
        } else {
          reviews.push({
            master_key: key,
            action: "verify_date",
            date: cb.dataset.date,
          });
        }
      });
      const add = card.querySelector(".add-date")?.value?.trim();
      if (add) {
        reviews.push({ master_key: key, action: "add_date", date: add });
      }
    });

    setStatus("جاري اعتماد التواريخ…");
    await api("/api/dates/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, reviews }),
    });

    setStatus("جاري تنفيذ الترتيب الحتمي…");
    const data = await api("/api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, auto_verify_dates: false }),
    });
    renderResults(data.results || [], data.summary || {});
    $("panel-results").classList.remove("hidden");
    setStep(4);
    wireExports();
    setStatus(data.messages?.slice(-1)[0] || "اكتمل الترتيب.");
  });

  function wireExports() {
    if (!sessionId) return;
    $("btnExcel").href = `/api/export/excel?session_id=${sessionId}`;
    $("btnPdf").href = `/api/export/pdf?session_id=${sessionId}`;
    $("btnPdfFormal").href = `/api/export/pdf/formal?session_id=${sessionId}`;
    $("btnMasterXlsx").href = `/api/export/master?session_id=${sessionId}`;
    if ($("btnText")) $("btnText").href = `/api/export/text?session_id=${sessionId}`;
    if ($("btnAudit")) $("btnAudit").href = `/api/export/audit?session_id=${sessionId}`;
  }

  if ($("btnCopyText")) {
    $("btnCopyText").onclick = withLoading($("btnCopyText"), async (ev) => {
      if (ev && ev.preventDefault) ev.preventDefault();
      await ensureSession();
      const res = await fetch(`/api/export/text?session_id=${sessionId}`);
      if (!res.ok) throw new Error("لا توجد نتائج لنسخها.");
      const text = await res.text();
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        // iOS fallback
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setStatus("نُسخ الترتيب — الصقه في واتساب.");
    });
  }

  if ($("btnTheme")) {
    const setThemeLabel = () => {
      const dark = document.body.classList.contains("dark");
      $("btnTheme").textContent = dark ? "نهاري" : "ليلي";
    };
    $("btnTheme").onclick = () => {
      document.body.classList.toggle("dark");
      const dark = document.body.classList.contains("dark");
      localStorage.setItem("tarteeb-theme", dark ? "dark" : "light");
      setThemeLabel();
    };
    if (localStorage.getItem("tarteeb-theme") === "dark") {
      document.body.classList.add("dark");
    }
    setThemeLabel();
  }

  if ($("btnReset")) {
    $("btnReset").onclick = withLoading($("btnReset"), async () => {
      await ensureSession();
      const data = await api("/api/session/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      sessionId = data.session_id;
      lastResults = [];
      datesPayload = [];
      $("panel-names").classList.add("hidden");
      $("panel-dates").classList.add("hidden");
      $("panel-results").classList.add("hidden");
      $("masterPreview").classList.add("hidden");
      $("masterInfo").textContent = "";
      $("targetInfo").textContent = "";
      $("btnTargets").disabled = true;
      $("btnManual").disabled = true;
      setStep(1);
      setStatus("جلسة جديدة — ارفع الملفات أو شغّل تجربة كاملة.");
    });
  }

  $("btnCompare").onclick = withLoading($("btnCompare"), async () => {
    await ensureSession();
    const a = $("cmpA").value.trim();
    const b = $("cmpB").value.trim();
    if (!a || !b) throw new Error("أدخل الاسمين للمقارنة.");
    const data = await api("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, a, b }),
    });
    let msg = "";
    if (data.result === "a") {
      msg = `يتقدّم «${data.a_name}» على «${data.b_name}» عند المستوى ${
        (data.level ?? 0) + 1
      } (${data.date_a} أقدم من ${data.date_b}).`;
    } else if (data.result === "b") {
      msg = `يتقدّم «${data.b_name}» على «${data.a_name}» عند المستوى ${
        (data.level ?? 0) + 1
      } (${data.date_b} أقدم من ${data.date_a}).`;
    } else if (data.result === "tie") {
      msg = `تعادل تام بين «${data.a_name}» و «${data.b_name}».`;
    } else {
      msg = `تعادل غير محسوم بين «${data.a_name}» و «${data.b_name}»: ${
        data.message || ""
      }`;
    }
    $("cmpOut").innerHTML = `
      <div class="review-card">
        <div>${escapeHtml(msg)}</div>
        <div class="muted" style="margin-top:0.4rem">تسلسل أ: ${(data.a_dates || [])
          .map(escapeHtml)
          .join(" ← ")}</div>
        <div class="muted">تسلسل ب: ${(data.b_dates || []).map(escapeHtml).join(" ← ")}</div>
      </div>`;
  });

  function renderRankChart(results) {
    const box = $("rankChart");
    if (!box) return;
    const ranked = (results || []).filter((r) => r.rank != null && r.latest_date);
    if (!ranked.length) {
      box.classList.add("hidden");
      box.innerHTML = "";
      return;
    }
    // visual: older latest date = longer bar (higher priority)
    const toKey = (d) => {
      if (!d) return 0;
      const p = String(d).replace(/-/g, "/").split("/");
      if (p.length < 3) return 0;
      return Number(p[0]) * 10000 + Number(p[1]) * 100 + Number(p[2]);
    };
    const keys = ranked.map((r) => toKey(r.latest_date)).filter(Boolean);
    const minK = Math.min(...keys);
    const maxK = Math.max(...keys);
    const span = Math.max(1, maxK - minK);
    box.classList.remove("hidden");
    box.innerHTML =
      `<div class="muted" style="margin-bottom:0.35rem">تصور أولوية أحدث تاريخ (شريط أطول = أحدث تاريخ أقدم = أولوية أعلى)</div>` +
      ranked
        .slice(0, 20)
        .map((r) => {
          const k = toKey(r.latest_date);
          // invert: older => higher pct
          const pct = 12 + ((maxK - k) / span) * 88;
          return `<div class="rank-bar-row">
            <span>#${r.rank}</span>
            <span class="nm" title="${escapeHtml(r.original_name || "")}">${escapeHtml(
            r.original_name || ""
          )}</span>
            <div class="rank-bar-track"><div class="rank-bar-fill" style="width:${pct}%"></div></div>
            <span class="dt muted">${escapeHtml(r.latest_date || "")}</span>
          </div>`;
        })
        .join("");
  }

  function renderResults(results, summary) {
    lastResults = results || [];
    renderRankChart(results);
    $("summaryGrid").innerHTML = [
      ["المطلوبة", summary.target_total ?? "—"],
      ["المؤكدة", summary.target_verified ?? "—"],
      ["تحتاج مراجعة", summary.target_needs_review ?? summary.skipped_needs_review ?? "—"],
      ["غير موجودة", summary.target_not_in_master ?? summary.not_found ?? "—"],
      ["مرتّبة بنجاح", summary.ranked_successfully ?? "—"],
      ["متعادلة", summary.tied ?? "—"],
      ["غير محسومة", summary.unresolved ?? "—"],
    ]
      .map(
        ([l, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`
      )
      .join("");

    // quality ribbon
    const rankedN = Number(summary.ranked_successfully || 0);
    const unresolved = Number(summary.unresolved || 0);
    const tied = Number(summary.tied || 0);
    const totalCand = Number(summary.ranked_candidates || rankedN || 1);
    const quality =
      unresolved > 0 ? "يحتاج مراجعة بشرية لبعض الحالات" :
      tied > 0 ? "ترتيب مكتمل مع تعادلات صريحة" :
      rankedN > 0 ? "ترتيب مكتمل وحاسم" : "لا نتائج مرتّبة بعد";
    const ribbon = document.createElement("div");
    ribbon.className = "hint";
    ribbon.style.marginTop = "0.5rem";
    ribbon.innerHTML = `<strong>جودة التشغيل:</strong> ${quality}` +
      (summary.auto_export_dir
        ? ` · نُسخ إلى <code>${escapeHtml(summary.auto_export_dir)}</code>`
        : "");
    const grid = $("summaryGrid");
    if (grid && grid.parentElement) {
      const old = grid.parentElement.querySelector(".quality-ribbon");
      if (old) old.remove();
      ribbon.classList.add("quality-ribbon");
      grid.after(ribbon);
    }

    const body = $("resultsBody");
    if (body) {
      body.innerHTML = results
        .map(
          (r, idx) => `
      <tr data-name="${escapeHtml(r.original_name || "")}">
        <td>${escapeHtml(r.rank_display || r.rank || "—")}</td>
        <td>
          <strong>${escapeHtml(r.original_name || "")}</strong>
          <div><button type="button" class="btn btn-outline" data-detail="${idx}">تفاصيل</button></div>
        </td>
        <td>${escapeHtml(r.latest_date || "—")}</td>
        <td>${escapeHtml(r.previous_date || "—")}</td>
        <td>${r.date_count ?? 0}</td>
        <td>${badge(r.status)}</td>
        <td class="explanation">${escapeHtml(r.explanation || "")}</td>
      </tr>`
        )
        .join("");
      body.querySelectorAll("[data-detail]").forEach((btn) => {
        btn.onclick = () => showDetail(Number(btn.dataset.detail));
      });
    }

    const cards = $("resultsCards");
    if (cards) {
      cards.innerHTML = results
        .map((r, idx) => {
          if (r.rank == null && !(r.status || "").includes("مرت")) {
            // still show non-ranked for transparency, compact
          }
          return `
        <article class="result-card" data-name="${escapeHtml(r.original_name || "")}">
          <div>
            <span class="rank-num">#${escapeHtml(r.rank_display || r.rank || "—")}</span>
            ${badge(r.status)}
          </div>
          <div class="card-title">${escapeHtml(r.original_name || "")}</div>
          <div class="meta-row">
            <span>أحدث: <strong>${escapeHtml(r.latest_date || "—")}</strong></span>
            <span>سابق: ${escapeHtml(r.previous_date || "—")}</span>
            <span>عدد: ${r.date_count ?? 0}</span>
          </div>
          <div class="explanation">${escapeHtml((r.explanation || "").slice(0, 160))}${(r.explanation || "").length > 160 ? "…" : ""}</div>
          <div class="card-actions">
            <button type="button" class="btn btn-outline" data-detail="${idx}">تفاصيل</button>
          </div>
        </article>`;
        })
        .join("");
      cards.querySelectorAll("[data-detail]").forEach((btn) => {
        btn.onclick = () => {
          showDetail(Number(btn.dataset.detail));
          $("detailBox")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        };
      });
    }

    const filter = $("resultsFilter");
    if (filter) {
      filter.oninput = () => {
        const q = filter.value.trim();
        if (body) {
          body.querySelectorAll("tr").forEach((tr) => {
            const name = tr.dataset.name || tr.querySelector("strong")?.textContent || "";
            tr.style.display = !q || name.includes(q) ? "" : "none";
          });
        }
        if (cards) {
          cards.querySelectorAll(".result-card").forEach((card) => {
            const name = card.dataset.name || "";
            card.style.display = !q || name.includes(q) ? "" : "none";
          });
        }
      };
    }

    const issues = results.filter(
      (r) =>
        r.status &&
        (r.status.includes("مراجعة") ||
          r.status.includes("محسوم") ||
          r.status.includes("تعادل") ||
          r.status.includes("غير موجود") ||
          r.status.includes("بدون"))
    );
    $("issuesBox").innerHTML = issues.length
      ? `<ul>${issues
          .map(
            (r) =>
              `<li><strong>${escapeHtml(r.original_name || "")}</strong> — ${escapeHtml(
                r.status
              )}: ${escapeHtml(r.explanation || "")}</li>`
          )
          .join("")}</ul>`
      : "لا توجد حالات معلّقة ظاهرة.";
  }

  function showDetail(idx) {
    const r = lastResults[idx];
    if (!r) return;
    const dates = (r.dates || []).map((d) => `<li>${escapeHtml(d)}</li>`).join("") || "<li>—</li>";
    const allDates = ((r.meta && r.meta.all_dates) || [])
      .map(
        (d) =>
          `<li>${d.source_image ? `<img src="${escapeHtml(d.source_image)}" alt="صف المصدر" loading="lazy" style="display:block;max-width:100%;max-height:150px;border:1px solid var(--border);border-radius:8px;margin:.35rem 0" />` : ""}${escapeHtml(d.display || d.normalized_date)} — ص ${d.page ?? "؟"} — ثقة ${Math.round(
            (d.confidence || 0) * 100
          )}% — اتفاق OCR: ${d.ocr_agreement ?? 1} — ارتباط الصف: ${Math.round((d.row_association_confidence || 0) * 100)}% ${d.needs_review ? "⚠ مراجعة" : ""} <span class="muted">${escapeHtml(
            d.original_text || ""
          )}</span></li>`
      )
      .join("");
    const notes = ((r.meta && r.meta.notes) || [])
      .slice(0, 4)
      .map((n) => `<li class="muted">${escapeHtml(n).slice(0, 220)}</li>`)
      .join("");
    $("detailBox").innerHTML = `
      <div class="review-card">
        <div><strong>${escapeHtml(r.original_name || "")}</strong> ${badge(r.status)} — ترتيب #${
      escapeHtml(r.rank_display || r.rank || "—")
    }</div>
        <p class="explanation">${escapeHtml(r.explanation || "")}</p>
        <h4 style="margin:0.6rem 0 0.3rem;color:var(--primary)">مفتاح الترتيب (الأحدث ← الأقدم)</h4>
        <ol>${dates}</ol>
        ${
          allDates
            ? `<h4 style="margin:0.6rem 0 0.3rem;color:var(--primary)">كل التواريخ المستخرجة مع المصدر</h4><ul>${allDates}</ul>`
            : ""
        }
        ${
          notes
            ? `<h4 style="margin:0.6rem 0 0.3rem;color:var(--primary)">مقتطفات الملاحظات</h4><ul>${notes}</ul>`
            : ""
        }
        <div class="muted">الصفحات: ${((r.meta && r.meta.pages) || []).join("، ") || "—"}</div>
      </div>`;
    $("detailBox").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
