/* ترتيب أبو علياء — واجهة مبسطة بقائمتين فقط */
(() => {
  "use strict";

  let sessionId = null;
  let targetIds = new Set();

  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setStatus(message, isError = false) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      const detail =
        data.detail || response.statusText || `فشل الطلب (HTTP ${response.status}).`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function filesSummary(files) {
    const selected = [...(files || [])];
    if (!selected.length) return "صورة أو PDF — ويمكن اختيار عدة صفحات.";
    if (selected.length === 1) return `تم اختيار: ${selected[0].name}`;
    return `تم اختيار ${selected.length} ملفات.`;
  }

  function updateFileSummaries() {
    $("masterInfo").textContent = filesSummary($("masterFile").files);
    $("targetInfo").textContent = filesSummary($("targetFile").files);
  }

  function badge(status) {
    const value = status || "غير معروف";
    let klass = "badge-info";
    if (value.includes("مرتّب") || value.includes("مؤكد")) klass = "badge-ok";
    else if (value.includes("مراجعة") || value.includes("غير موجود") || value.includes("محسوم")) {
      klass = "badge-danger";
    } else if (value.includes("تعادل")) klass = "badge-warn";
    return `<span class="badge ${klass}">${escapeHtml(value)}</span>`;
  }

  function renderResults(allResults, summary) {
    // حماية إضافية في الواجهة: لا نعرض إلا صفوفًا مصدرها القائمة الثانية.
    const results = (allResults || []).filter((item) => targetIds.has(item.id));
    const rankedCount = results.filter((item) => item.rank != null).length;
    const reviewCount = results.length - rankedCount;

    $("summaryGrid").innerHTML = [
      ["أسماء القائمة الثانية", results.length],
      ["تم ترتيبها", rankedCount],
      ["تحتاج مراجعة", reviewCount],
    ]
      .map(([label, value]) => `<div class="stat"><div class="n">${value}</div><div class="l">${label}</div></div>`)
      .join("");

    const notice = $("resultNotice");
    if (!results.length) {
      notice.textContent = "لم يُستخرج أي اسم صالح من القائمة الثانية. جرّب صورة أو PDF أوضح.";
      notice.classList.add("error");
    } else if (reviewCount) {
      notice.textContent = `تم ترتيب ${rankedCount} اسمًا، وتوقّف ${reviewCount} اسمًا لحماية الدقة بسبب غموض الاسم أو التاريخ.`;
      notice.classList.remove("error");
    } else {
      notice.textContent = "اكتمل الترتيب، وكل الأسماء الظاهرة مأخوذة من القائمة الثانية فقط.";
      notice.classList.remove("error");
    }

    $("resultsCards").innerHTML = results
      .map((item) => {
        const rank = item.rank_display || item.rank;
        const isRanked = rank != null;
        return `
          <article class="result-card simple-result-card ${isRanked ? "" : "result-pending"}">
            <div class="result-rank">${isRanked ? `#${escapeHtml(rank)}` : "—"}</div>
            <div class="result-main">
              <div class="result-name">${escapeHtml(item.original_name || "")}</div>
              <div class="result-meta">
                ${badge(item.status)}
                <span>أحدث تاريخ: <strong>${escapeHtml(item.latest_date || "—")}</strong></span>
                <span>السابق: ${escapeHtml(item.previous_date || "—")}</span>
              </div>
              ${isRanked ? "" : `<p class="result-reason">${escapeHtml(item.explanation || "يحتاج مراجعة قبل الترتيب.")}</p>`}
            </div>
          </article>`;
      })
      .join("");

    $("panel-results").classList.remove("hidden");
    $("exportRow").classList.toggle("hidden", !results.length);
    wireExports();
    $("panel-results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function wireExports() {
    if (!sessionId) return;
    $("btnExcel").href = `/api/export/excel?session_id=${encodeURIComponent(sessionId)}`;
    $("btnPdfFormal").href = `/api/export/pdf/formal?session_id=${encodeURIComponent(sessionId)}`;
  }

  async function uploadMany(url, fieldName, files) {
    const formData = new FormData();
    [...files].forEach((file) => formData.append(fieldName, file));
    return api(url, { method: "POST", body: formData });
  }

  async function runRanking() {
    const masterFiles = [...($("masterFile").files || [])];
    const targetFiles = [...($("targetFile").files || [])];
    if (!masterFiles.length) throw new Error("اختر قائمة الأسماء والتواريخ في الخيار رقم ١.");
    if (!targetFiles.length) throw new Error("اختر قائمة الأسماء المراد ترتيبهم في الخيار رقم ٢.");

    const session = await api("/api/session", { method: "POST" });
    sessionId = session.session_id;

    setStatus("١/٤ — جاري استخراج الأسماء والتواريخ من القائمة الأولى…");
    const master = await uploadMany(
      `/api/upload/master/multi?session_id=${encodeURIComponent(sessionId)}`,
      "files",
      masterFiles
    );
    if (!master.master_people_count) {
      throw new Error("لم يُستخرج أي اسم وتاريخ من القائمة الأولى. جرّب ملفًا أوضح.");
    }

    setStatus("٢/٤ — جاري استخراج الأسماء المطلوبة من القائمة الثانية…");
    const targets = await uploadMany(
      `/api/upload/targets/multi?session_id=${encodeURIComponent(sessionId)}`,
      "files",
      targetFiles
    );
    targetIds = new Set((targets.target_names || []).map((item) => item.id));
    if (!targetIds.size) {
      throw new Error("لم يُستخرج أي اسم من القائمة الثانية. جرّب صورة أو PDF أوضح.");
    }

    setStatus("٣/٤ — جاري اعتماد التطابقات عالية الثقة فقط…");
    await api("/api/names/auto_confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, min_confidence: 0.97 }),
    });

    setStatus("٤/٤ — جاري ترتيب أسماء القائمة الثانية بالتواريخ المعتمدة…");
    const ranked = await api("/api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, auto_verify_dates: true }),
    });

    renderResults(ranked.results || [], ranked.summary || {});
    setStatus("اكتمل الترتيب. النتيجة أدناه لأسماء القائمة الثانية فقط.");
  }

  $("masterFile").addEventListener("change", updateFileSummaries);
  $("targetFile").addEventListener("change", updateFileSummaries);

  $("rankForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("btnRankFiles");
    const original = button.innerHTML;
    button.disabled = true;
    button.classList.add("is-loading");
    button.innerHTML = '<span class="loader"></span> جاري الاستخراج والترتيب…';
    $("panel-results").classList.add("hidden");
    try {
      await runRanking();
    } catch (error) {
      setStatus(error.message || String(error), true);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.innerHTML = original;
    }
  });

  $("btnReset").addEventListener("click", () => {
    sessionId = null;
    targetIds = new Set();
    $("rankForm").reset();
    updateFileSummaries();
    $("panel-results").classList.add("hidden");
    setStatus("اختر القائمتين ثم اضغط «استخراج وترتيب الأسماء».");
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  $("btnCopyText").addEventListener("click", async (event) => {
    event.preventDefault();
    if (!sessionId) return;
    try {
      const response = await fetch(`/api/export/text?session_id=${encodeURIComponent(sessionId)}`);
      if (!response.ok) throw new Error("لا توجد نتيجة لنسخها.");
      const text = await response.text();
      await navigator.clipboard.writeText(text);
      setStatus("تم نسخ نتيجة الترتيب.");
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  });
})();
