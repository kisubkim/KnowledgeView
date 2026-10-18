"use strict";
(function () {
  const $ = (s, c) => (c || document).querySelector(s);
  const $$ = (s, c) => Array.from((c || document).querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const view = $("#view");
  const MODE_SHORT = { none: "구조화", claude_cli: "Claude CLI", openai: "OpenAI 호환" };
  function modeShort(mode) {
    if (MODE_SHORT[mode]) return MODE_SHORT[mode];
    const m = status && status.modes.find((x) => x.id === mode);
    if (m) return m.label.replace(/^OpenAI 호환 상세화 \((.*)\)$/, "OpenAI: $1");
    return mode && mode.startsWith("openai:") ? "OpenAI 호환 (삭제된 엔드포인트)" : (mode || "?");
  }
  let status = null;
  let pollTimer = null;

  // ---------------------------------------------------------------- helpers
  function toast(msg, err) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (err ? " err" : "");
    clearTimeout(t._h);
    t._h = setTimeout(() => (t.className = "toast"), err ? 5000 : 2500);
  }

  function token() { try { return localStorage.getItem("kv-token") || ""; } catch (e) { return ""; } }

  async function api(method, url, body, isForm) {
    const opts = { method, headers: { "X-KV-Token": token() } };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    }
    const r = await fetch(url, opts);
    if (r.status === 401) {
      const t = await ask("관리 토큰", '<label class="field"><span>서버의 KV_ADMIN_TOKEN 값을 입력하세요.</span><input type="password" name="token" autofocus></label>');
      if (t && t.token) { try { localStorage.setItem("kv-token", t.token); } catch (e) {} return api(method, url, body, isForm); }
      throw new Error("관리 토큰이 필요합니다.");
    }
    const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
    return data;
  }

  // Dialog: resolves to an object of named inputs on OK, or null.
  function ask(title, bodyHtml, okLabel) {
    return new Promise((resolve) => {
      const d = $("#dlg");
      $("#dlg-title").textContent = title;
      $("#dlg-body").innerHTML = bodyHtml;
      $("#dlg-ok").textContent = okLabel || "확인";
      d.onclose = () => {
        if (d.returnValue !== "ok") return resolve(null);
        const out = {};
        $$("input,select,textarea", d).forEach((el) => { if (el.name) out[el.name] = el.type === "checkbox" ? el.checked : el.value; });
        resolve(out);
      };
      d.returnValue = "";
      d.showModal();
      const f = $("[autofocus]", d); if (f) f.focus();
    });
  }

  function fmtTime(s) { return s ? s.replace("T", " ").slice(0, 16) : ""; }
  function catLabel(c) { return c || "(최상위)"; }

  async function loadStatus() { status = await api("GET", "/api/status"); return status; }

  function modeRadios(name, selected) {
    return '<div class="modes">' + status.modes.map((m) =>
      `<label class="mode${m.available ? "" : " off"}"><input type="radio" name="${name}" value="${m.id}" ${m.id === selected ? "checked" : ""} ${m.available ? "" : "disabled"}>` +
      `<span><b>${esc(m.label)}</b><small>${esc(m.note || "원문을 템플릿으로 재구성")}</small></span></label>`).join("") + "</div>";
  }

  function categoryOptions(cats, selected) {
    return cats.map((c) => `<option value="${esc(c.path)}" ${c.path === selected ? "selected" : ""}>${esc(catLabel(c.path))}</option>`).join("");
  }

  // ---------------------------------------------------------------- library
  async function renderLibrary(cat) {
    const lib = await api("GET", "/api/library");
    const filterCat = cat === undefined ? null : cat;
    const q = (sessionStorage.getItem("kv-q") || "").toLowerCase();
    let pages = lib.pages;
    if (filterCat !== null) pages = pages.filter((p) => p.category === filterCat || p.category.startsWith(filterCat + "/"));
    if (q) pages = pages.filter((p) => (p.title + " " + p.rel).toLowerCase().includes(q));

    const catItems = lib.categories.filter((c) => c.path !== "" || c.pages > 0).map((c) => {
      const depth = c.path ? c.path.split("/").length - 1 : 0;
      const name = c.path ? c.path.split("/").pop() : "(최상위)";
      const total = lib.pages.filter((p) => p.category === c.path || (c.path && p.category.startsWith(c.path + "/"))).length;
      return `<li><a href="#/library/${encodeURIComponent(c.path)}" class="${filterCat === c.path ? "active" : ""}" style="padding-left:${10 + depth * 14}px"><span>${esc(name)}</span><span class="n">${total}</span></a></li>`;
    }).join("");

    view.innerHTML = `
      <div class="split">
        <aside class="panel cats">
          <div class="head"><b>카테고리</b><button class="btn sm" id="cat-new">+ 새 카테고리</button></div>
          <ul><li><a href="#/library" class="${filterCat === null ? "active" : ""}"><span>전체</span><span class="n">${lib.pages.length}</span></a></li>${catItems}</ul>
          ${filterCat ? `<div class="row" style="margin-top:12px"><button class="btn sm" id="cat-ren">이름 변경</button><button class="btn sm danger" id="cat-del">삭제</button></div>` : ""}
          <p class="hint" style="margin:14px 6px 0">출력 폴더: <code>${esc(lib.output_dir)}</code></p>
        </aside>
        <section>
          <div class="row" style="margin-bottom:14px">
            <input type="search" id="q" class="grow" placeholder="제목/경로 검색" value="${esc(q)}">
            <a class="btn primary" href="#/generate">+ 페이지 생성</a>
          </div>
          ${pages.length ? `<div class="cards">${pages.map(cardHtml).join("")}</div>` :
            `<div class="panel empty">${lib.pages.length ? "조건에 맞는 페이지가 없습니다." : "아직 생성된 페이지가 없습니다. <a href='#/generate'>생성</a> 탭에서 MD 파일을 골라 만드세요."}</div>`}
        </section>
      </div>`;

    $("#q").oninput = (e) => { sessionStorage.setItem("kv-q", e.target.value); clearTimeout(e.target._h); e.target._h = setTimeout(() => renderLibrary(cat).then(() => { const i = $("#q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }), 250); };
    $("#cat-new").onclick = async () => {
      const r = await ask("새 카테고리", `<label class="field"><span>이름 (하위 카테고리는 / 로 구분, 예: AI/LLM)</span><input type="text" name="path" value="${filterCat ? esc(filterCat) + "/" : ""}" autofocus></label>`, "만들기");
      if (!r || !r.path.trim()) return;
      try { const c = await api("POST", "/api/categories", { path: r.path }); toast("카테고리를 만들었습니다."); location.hash = "#/library/" + encodeURIComponent(c.path); } catch (e) { toast(e.message, true); }
    };
    if (filterCat) {
      $("#cat-ren").onclick = async () => {
        const r = await ask("카테고리 이름 변경", `<label class="field"><span>새 경로</span><input type="text" name="new_path" value="${esc(filterCat)}" autofocus></label><p class="hint">페이지 안의 다른 문서 링크는 원본 폴더 구조 기준이라, 옮기면 링크가 맞지 않을 수 있습니다.</p>`);
        if (!r) return;
        try { const c = await api("PATCH", "/api/categories", { path: filterCat, new_path: r.new_path }); location.hash = "#/library/" + encodeURIComponent(c.path); } catch (e) { toast(e.message, true); }
      };
      $("#cat-del").onclick = async () => {
        const n = lib.pages.filter((p) => p.category === filterCat || p.category.startsWith(filterCat + "/")).length;
        const r = await ask("카테고리 삭제", `<p><b>${esc(filterCat)}</b> 카테고리를 삭제합니다.${n ? ` 안에 있는 페이지 <b>${n}개</b>도 함께 삭제됩니다.` : ""} 원본 MD는 지워지지 않습니다.</p>`, "삭제");
        if (!r) return;
        try { await api("DELETE", `/api/categories?path=${encodeURIComponent(filterCat)}&force=true`); toast("삭제했습니다."); location.hash = "#/library"; } catch (e) { toast(e.message, true); }
      };
    }
    $$("[data-act]").forEach((b) => (b.onclick = () => pageAction(b.dataset.act, b.dataset.rel, lib)));
  }

  function cardHtml(p) {
    const href = "/view/" + p.rel.split("/").map(encodeURIComponent).join("/");
    const nfig = Object.values(p.figures || {}).reduce((x, y) => x + y, 0);
    const badges = [`<span class="badge ${p.mode && p.mode !== "none" ? "llm" : ""}">${esc(modeShort(p.mode))}</span>`];
    if (p.stale === true) badges.push('<span class="badge warn" title="원본 MD가 생성 후 바뀌었습니다">원본 변경됨</span>');
    if (p.stale === "missing") badges.push('<span class="badge err" title="원본 MD를 찾을 수 없습니다">원본 없음</span>');
    if (p.warnings && p.warnings.length) badges.push(`<span class="badge warn" title="${esc(p.warnings.join("\n"))}">경고 ${p.warnings.length}</span>`);
    return `<article class="card">
      <a class="t" href="${href}">${esc(p.title)}</a>
      <div class="row">${badges.join("")}</div>
      <div class="sub">${esc(catLabel(p.category))} · ${esc(fmtTime(p.generated_at))}<br><span title="${esc(p.source_path || "")}">원본: ${esc(p.source_rel || "-")}</span></div>
      <div class="acts">
        <a class="btn sm" href="${href}">열기</a>
        <a class="btn sm" href="${href}" target="_blank" rel="noopener">새 탭</a>
        <button class="btn sm" data-act="regen" data-rel="${esc(p.rel)}">재생성</button>
        <button class="btn sm" data-act="move" data-rel="${esc(p.rel)}">이동</button>
        ${p.history ? `<button class="btn sm" data-act="history" data-rel="${esc(p.rel)}">이전 버전 ${p.history}</button>` : ""}
        ${nfig ? `<a class="btn sm" href="/api/pages/figures.zip?rel=${encodeURIComponent(p.rel)}" title="그림 ${nfig}개와 다이어그램 원본을 ZIP으로 받기">그림 내보내기</a>` : ""}
        <button class="btn sm danger" data-act="del" data-rel="${esc(p.rel)}">삭제</button>
      </div></article>`;
  }

  async function pageAction(act, rel, lib) {
    const page = lib.pages.find((p) => p.rel === rel);
    try {
      if (act === "history") return showHistory(page);
      if (act === "del") {
        if (!(await ask("페이지 삭제", `<p><b>${esc(page.title)}</b> 페이지(HTML)를 삭제합니다.${page.history ? ` 이전 버전 ${page.history}개도 함께 삭제됩니다.` : ""} 원본 MD는 그대로 둡니다.</p>`, "삭제"))) return;
        await api("DELETE", "/api/pages?rel=" + encodeURIComponent(rel));
        toast("삭제했습니다.");
      } else if (act === "move") {
        const r = await ask("다른 카테고리로 이동", `<label class="field"><span>기존 카테고리</span><select name="cat">${categoryOptions(lib.categories, page.category)}</select></label><label class="field"><span>또는 새 카테고리 (입력하면 우선)</span><input type="text" name="newcat" placeholder="예: AI/LLM"></label>`, "이동");
        if (!r) return;
        await api("POST", "/api/pages/move", { rel, category: r.newcat.trim() || r.cat });
        toast("이동했습니다.");
      } else if (act === "regen") {
        await loadStatus();
        const r = await ask("페이지 재생성", `<p class="hint">원본: ${esc(page.source_path || "")}</p>${modeRadios("mode", page.mode || status.default_mode)}<label class="row" style="margin-top:12px"><input type="checkbox" name="refresh"> LLM 결과 새로 받기 (캐시 무시)</label>`, "재생성");
        if (!r) return;
        const mode = $$('#dlg input[name=mode]').find((i) => i.checked)?.value || status.modes.find((m) => m.available).id;
        await api("POST", "/api/pages/regenerate", { rels: [rel], mode, refresh: !!r.refresh });
        toast("작업을 등록했습니다.");
        location.hash = "#/jobs";
        return;
      }
      route();
    } catch (e) { toast(e.message, true); }
  }

  async function showHistory(page) {
    const { versions } = await api("GET", "/api/pages/history?rel=" + encodeURIComponent(page.rel));
    const href = (rel) => "/view/" + rel.split("/").map(encodeURIComponent).join("/");
    const rows = versions.map((v) => `<tr>
        <td>${esc(fmtTime(v.generated_at) || v.version)}</td>
        <td>${esc(modeShort(v.mode))}${v.model ? `<br><span class="hint">${esc(v.model)}</span>` : ""}</td>
        <td class="hint">${(v.size / 1024).toFixed(0)}KB${v.warnings.length ? ` · 경고 ${v.warnings.length}` : ""}</td>
        <td style="white-space:nowrap"><a class="btn sm" href="${href(v.rel)}" target="_blank" rel="noopener">열기</a>
          <button type="button" class="btn sm" data-h="restore" data-v="${esc(v.version)}">복원</button>
          <button type="button" class="btn sm danger" data-h="del" data-v="${esc(v.version)}">삭제</button></td></tr>`).join("");
    const done = ask("이전 버전: " + page.title,
      `<p class="hint">재생성으로 덮어쓴 페이지입니다. 복원하면 현재 페이지가 이전 버전으로 옮겨지고 고른 버전이 현재 페이지가 됩니다.</p>
       <div class="kv-hist" style="max-height:60vh;overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:14px">
       <thead><tr><th style="text-align:left">생성 시각</th><th style="text-align:left">방식</th><th></th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="hint">이전 버전이 없습니다.</td></tr>'}</tbody></table></div>`, "닫기");
    $$("#dlg [data-h]").forEach((b) => (b.onclick = async () => {
      const v = b.dataset.v;
      try {
        if (b.dataset.h === "restore") {
          await api("POST", "/api/pages/history/restore", { rel: page.rel, version: v });
          toast("복원했습니다. 이전 현재 페이지는 이전 버전에 보관했습니다.");
        } else {
          await api("DELETE", `/api/pages/history?rel=${encodeURIComponent(page.rel)}&version=${encodeURIComponent(v)}`);
          toast("이전 버전을 삭제했습니다.");
        }
        $("#dlg").close();
        route();
      } catch (e) { toast(e.message, true); }
    }));
    await done;
  }

  // ---------------------------------------------------------------- generate
  async function renderGenerate() {
    await loadStatus();
    const [src, lib] = await Promise.all([api("GET", "/api/sources"), api("GET", "/api/library")]);
    const defMode = status.modes.find((m) => m.id === status.default_mode && m.available) ? status.default_mode : "none";

    const rootsHtml = src.roots.map((root) => {
      if (root.is_uploads && !root.files.length) return "";
      const groups = {};
      root.files.forEach((f) => { const d = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) : ""; (groups[d] = groups[d] || []).push(f); });
      const title = root.is_uploads ? "업로드한 MD" : root.root;
      if (!root.exists) return `<div class="panel"><b>${esc(title)}</b> <span class="badge err">폴더 없음</span></div>`;
      return `<h3>${esc(title)} <span class="hint">(${root.files.length}개)</span></h3><div class="tree">` + Object.keys(groups).sort().map((d) =>
        `<details ${d === "" || Object.keys(groups).length <= 3 ? "open" : ""}><summary><input type="checkbox" class="grp" data-root="${root.index}" data-dir="${esc(d)}"> 📁 ${esc(d || "(루트)")} <span class="hint">${groups[d].length}</span></summary>` +
        groups[d].map((f) => `<label class="file"><input type="checkbox" class="src" data-root="${root.index}" data-rel="${esc(f.rel)}" data-dir="${esc(d)}">
          <span class="name">${esc(f.rel.split("/").pop())}</span>
          ${f.pages.length ? `<span class="badge ok" title="${esc(f.pages.join("\n"))}">생성됨 ${f.pages.length}</span>` : ""}
          <span class="hint">${(f.size / 1024).toFixed(1)}KB</span></label>`).join("") + "</details>").join("") + "</div>";
    }).join("");

    view.innerHTML = `
      <div class="panel">
        <h2>1. 상세화 방식</h2>
        ${modeRadios("mode", defMode)}
        <p class="hint">LLM 모드는 섹션마다 해설·예시·비유·주의점과 그림(웹 공개 이미지, 없으면 LLM이 그린 SVG)을 덧붙이고 요약·용어 사전·확인 문제를 만듭니다. 원문 내용은 그대로 유지됩니다. 설정은 <a href="#/settings">설정</a> 탭에서 바꿉니다.</p>
        <label class="row"><input type="checkbox" id="refresh"> LLM 결과 새로 받기 (캐시 무시. 끄면 같은 원문·설정의 이전 결과를 재사용)</label>
      </div>
      <div class="panel">
        <h2>2. 저장할 카테고리</h2>
        <label class="row"><input type="radio" name="catmode" value="mirror" checked> 원본 폴더 구조를 그대로 따름 (예: <code>01_LLM_기초_이론/x.md</code> → 카테고리 <code>01_LLM_기초_이론</code>)</label>
        <label class="row" style="margin-top:8px"><input type="radio" name="catmode" value="fixed"> 지정한 카테고리에 모두 저장:
          <select id="cat-sel" style="width:auto">${categoryOptions(lib.categories, "")}</select>
          또는 새 이름 <input type="text" id="cat-new" style="width:200px" placeholder="예: AI/LLM"></label>
      </div>
      <div class="panel">
        <h2>3. 원본 MD 선택</h2>
        <div class="row"><button class="btn sm" id="sel-none">선택 해제</button><button class="btn sm" id="sel-new">아직 생성 안 한 것만 선택</button><span class="grow"></span><span id="sel-count" class="hint">0개 선택</span><button class="btn primary" id="go" disabled>선택한 문서 생성</button></div>
        ${rootsHtml || '<p class="empty">원본 폴더가 없습니다. 설정 탭에서 원본 폴더를 추가하세요.</p>'}
      </div>
      <div class="panel">
        <h2>MD 파일 올리기</h2>
        <p class="hint">다른 PC에서 접속한 경우 MD 파일을 직접 올릴 수 있습니다. 서버의 <code>data/uploads/&lt;카테고리&gt;/</code>에 저장되고, 위에서 고른 방식과 카테고리(구조 따름이면 최상위)로 바로 생성합니다.</p>
        <div class="drop" id="drop">여기에 .md 파일을 끌어 놓거나 클릭해서 고르세요<input type="file" id="file" accept=".md" multiple hidden></div>
      </div>`;

    const boxes = $$("input.src");
    const sync = () => {
      const n = boxes.filter((b) => b.checked).length;
      $("#sel-count").textContent = n + "개 선택"; $("#go").disabled = !n;
      $$("input.grp").forEach((g) => { const m = boxes.filter((b) => b.dataset.root === g.dataset.root && b.dataset.dir === g.dataset.dir); const c = m.filter((b) => b.checked).length; g.checked = c === m.length && c > 0; g.indeterminate = c > 0 && c < m.length; });
    };
    boxes.forEach((b) => (b.onchange = sync));
    $$("input.grp").forEach((g) => { g.onclick = (e) => e.stopPropagation(); g.onchange = () => { boxes.filter((b) => b.dataset.root === g.dataset.root && b.dataset.dir === g.dataset.dir).forEach((b) => (b.checked = g.checked)); sync(); }; });
    $("#sel-none").onclick = () => { boxes.forEach((b) => (b.checked = false)); sync(); };
    $("#sel-new").onclick = () => { boxes.forEach((b) => (b.checked = !b.closest(".file").querySelector(".badge.ok"))); sync(); };
    $("#cat-new").oninput = () => { $('input[name=catmode][value=fixed]').checked = true; };
    $("#cat-sel").onchange = () => { $('input[name=catmode][value=fixed]').checked = true; };

    const chosenMode = () => $$("input[name=mode]").find((i) => i.checked)?.value || "none";
    const chosenCat = () => $("input[name=catmode]:checked").value === "mirror" ? null : ($("#cat-new").value.trim() || $("#cat-sel").value);

    $("#go").onclick = async () => {
      const cat = chosenCat();
      const items = boxes.filter((b) => b.checked).map((b) => ({ root: +b.dataset.root, rel: b.dataset.rel, category: cat }));
      try {
        const r = await api("POST", "/api/generate", { items, mode: chosenMode(), refresh: $("#refresh").checked });
        toast(`${r.jobs.length}개 작업을 등록했습니다.`);
        location.hash = "#/jobs";
      } catch (e) { toast(e.message, true); }
    };

    const drop = $("#drop"), file = $("#file");
    const send = async (files) => {
      const md = Array.from(files).filter((f) => f.name.toLowerCase().endsWith(".md"));
      if (!md.length) return toast("MD 파일만 올릴 수 있습니다.", true);
      const fd = new FormData();
      md.forEach((f) => fd.append("files", f));
      fd.append("category", chosenCat() || "");
      fd.append("mode", chosenMode());
      fd.append("generate_now", "true");
      fd.append("refresh", $("#refresh").checked ? "true" : "false");
      try { const r = await api("POST", "/api/upload", fd, true); toast(`${r.saved.length}개를 올리고 작업을 등록했습니다.`); location.hash = "#/jobs"; } catch (e) { toast(e.message, true); }
    };
    drop.onclick = () => file.click();
    file.onchange = () => send(file.files);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files); };
  }

  // ---------------------------------------------------------------- jobs
  // The list is updated in place (no innerHTML rebuild on each poll): rebuilding reset the
  // log boxes' own scroll position to the top every 1.5 s and dropped text selections.
  const openLogs = new Set();
  const JOB_STATUS = { queued: "대기", running: "진행 중", done: "완료", failed: "실패", cancelled: "취소" };
  const JOB_BADGE = { queued: "", running: "llm", done: "ok", failed: "err", cancelled: "warn" };

  function jobHeadHtml(j) {
    const href = j.result ? "/view/" + j.result.rel.split("/").map(encodeURIComponent).join("/") : "";
    return `<span class="badge ${JOB_BADGE[j.status]}">${JOB_STATUS[j.status]}</span><b class="grow">${esc(j.source)}</b>
      <span class="hint">→ ${esc(catLabel(j.category))} · ${esc(modeShort(j.mode))}</span>
      ${href ? `<a class="btn sm primary" href="${href}">열기</a>` : ""}
      ${["queued", "running"].includes(j.status) ? `<button class="btn sm danger" data-cancel="${j.id}">취소</button>` : ""}
      <button class="btn sm" data-log="${j.id}" aria-pressed="${openLogs.has(j.id)}">로그</button>`;
  }

  function setHtml(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }

  function updateJobBox(box, j) {
    box.className = "job " + j.status;
    setHtml(box.querySelector(".job-head"), jobHeadHtml(j));
    const pct = j.status === "done" ? 100 : j.total ? Math.round((j.done / j.total) * 100) : 0;
    box.querySelector(".bar i").style.width = pct + "%";
    setHtml(box.querySelector(".job-msg"), `${esc(j.message)}${j.total ? ` (${j.done}/${j.total})` : ""} · ${esc(fmtTime(j.created_at))}`);
    let pre = box.querySelector("pre");
    if (!openLogs.has(j.id)) { if (pre) pre.remove(); return; }
    const text = j.log.join("\n");
    if (!pre) {
      pre = document.createElement("pre");
      box.appendChild(pre);
      pre.textContent = text;
      pre.scrollTop = pre.scrollHeight;  // newly opened: show the latest lines
      return;
    }
    if (pre.textContent === text) return;
    // Follow new lines only when the reader is already at the bottom; otherwise keep their place.
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
    const top = pre.scrollTop;
    pre.textContent = text;
    pre.scrollTop = atBottom ? pre.scrollHeight : top;
  }

  async function renderJobs(jobs) {
    if (!jobs) jobs = (await api("GET", "/api/jobs")).jobs;
    let list = $("#job-list");
    if (!list) {
      view.innerHTML = `<div class="panel"><div class="row"><h2 class="grow" style="margin:0">작업</h2><button class="btn sm" id="clear">끝난 작업 지우기</button></div></div>
        <div id="job-list"></div><div class="panel empty" id="job-empty" hidden>작업이 없습니다.</div>`;
      list = $("#job-list");
      $("#clear").onclick = async () => { await api("DELETE", "/api/jobs"); renderJobs(); };
      list.addEventListener("click", async (e) => {
        const cancel = e.target.closest("[data-cancel]"), log = e.target.closest("[data-log]");
        if (cancel) {
          try { await api("POST", `/api/jobs/${cancel.dataset.cancel}/cancel`); renderJobs(); } catch (err) { toast(err.message, true); }
        } else if (log) {
          const id = log.dataset.log;
          openLogs.has(id) ? openLogs.delete(id) : openLogs.add(id);
          renderJobs();
        }
      });
    }
    const keep = new Set(jobs.map((j) => j.id));
    $$(".job", list).forEach((box) => { if (!keep.has(box.dataset.job)) box.remove(); });
    let prev = null;
    jobs.forEach((j) => {
      let box = list.querySelector(`.job[data-job="${j.id}"]`);
      if (!box) {
        box = document.createElement("div");
        box.dataset.job = j.id;
        box.innerHTML = '<div class="row job-head"></div><div class="bar"><i></i></div><div class="hint job-msg"></div>';
      }
      // Keep the server's order (newest first) without moving boxes that are already in place.
      const want = prev ? prev.nextSibling : list.firstChild;
      if (want !== box) list.insertBefore(box, want);
      updateJobBox(box, j);
      prev = box;
    });
    $("#job-empty").hidden = jobs.length > 0;
    return jobs;
  }

  async function pollJobs() {
    try {
      const { jobs } = await fetch("/api/jobs").then((r) => r.json());
      const active = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
      const pill = $("#jobs-active"); pill.hidden = !active; pill.textContent = active;
      if (currentTab() === "jobs") await renderJobs(jobs);
      clearTimeout(pollTimer);
      pollTimer = setTimeout(pollJobs, active || currentTab() === "jobs" ? 1500 : 5000);
    } catch (e) { pollTimer = setTimeout(pollJobs, 5000); }
  }

  // ---------------------------------------------------------------- settings
  function claudeStateHtml(c) {
    if (!c) return "";
    if (c.installed && c.logged_in !== false) {
      return `<span class="badge ok">설치됨 · 로그인됨${c.subscription ? " (" + esc(c.subscription) + ")" : ""}</span>`;
    }
    return `<span class="badge warn">${c.installed ? "설치됨 · 로그인 안 됨" : "설치 안 됨"}</span> <span class="hint">${esc(c.note || "")} Claude Code 방식은 표시되지 않습니다.</span>`;
  }

  function epCard(ep) {
    const f = (k, label, type, extra) => `<label class="field${type === "text" ? " grow" : ""}"><span>${label}</span><input type="${type}" data-k="${k}" value="${esc(ep[k])}" ${extra || ""}></label>`;
    return `<div class="ep panel" data-id="${esc(ep.id)}" style="background:var(--surface-2);box-shadow:none;margin:12px 0">
      <div class="row">${f("name", "이름 (생성 화면에 표시)", "text", 'placeholder="예: 사내 vLLM Qwen3"')}${ep.id ? `<span class="hint">ID ${esc(ep.id)}</span>` : '<span class="badge warn">새 항목</span>'}</div>
      <div class="row">${f("base_url", "Base URL (예: http://127.0.0.1:8000/v1)", "text")}${f("model", "모델 이름", "text")}</div>
      <label class="field"><span>API 키 ${ep.has_api_key ? "(저장됨, 비워 두면 유지)" : "(없으면 비워 둠)"}</span><input type="password" data-k="api_key" autocomplete="off"></label>
      ${ep.has_api_key ? '<label class="row"><input type="checkbox" data-k="clear_api_key"> 저장된 API 키 삭제</label>' : ""}
      <div class="row"><label class="field"><span>추론 강도 (reasoning_effort)</span><select data-k="reasoning_effort">${["", "none", "low", "medium", "high"].map((v) => `<option value="${v}" ${(ep.reasoning_effort || "") === v ? "selected" : ""}>${v || "보내지 않음 (서버 기본)"}</option>`).join("")}</select></label>
        <span class="hint grow" style="align-self:center">추론(thinking) 모델은 답 전에 토큰을 많이 써서 max_tokens에 걸리거나 느려집니다. Ollama에서는 none이면 추론을 끕니다.</span></div>
      <div class="row">${f("temperature", "temperature", "number", 'step="0.1"')}${f("max_tokens", "max_tokens", "number")}${f("timeout_s", "제한 시간(초)", "number")}${f("concurrency", "동시 호출 수", "number", 'min="1" max="8"')}</div>
      <div class="row"><button type="button" class="btn sm ep-test">저장 후 연결 확인</button><span class="hint ep-out grow"></span><button type="button" class="btn sm danger ep-del">삭제</button></div>
    </div>`;
  }

  async function renderSettings() {
    await loadStatus();
    const [s, srv] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/server")]);
    const c = s.claude;
    view.innerHTML = `
      <form id="sf">
      <div class="panel"><h2>폴더</h2>
        <label class="field"><span>원본 MD 폴더 (한 줄에 하나, 읽기만 합니다)</span><textarea name="source_roots">${esc(s.source_roots.join("\n"))}</textarea></label>
        <label class="field"><span>출력 폴더 (HTML 페이지가 카테고리별 하위 폴더로 저장됨. 상대 경로는 앱 폴더 기준)</span><input type="text" name="output_dir" value="${esc(s.output_dir)}"></label>
        <p class="hint">현재 출력 위치: <code>${esc(s.output_dir_resolved)}</code></p>
        <label class="field" style="max-width:420px"><span>페이지당 보관할 이전 버전 수 (덮어쓴 페이지는 <code>.history/</code>로 이동, 0 = 모두 보관)</span><input type="number" min="0" name="history_keep" value="${s.history_keep}"></label>
      </div>
      <div class="panel"><h2>서버</h2>
        <div class="row">
          <label class="field" style="max-width:160px"><span>포트</span><input type="number" id="srv-port" min="1024" max="65535" value="${srv.configured_port}" ${srv.local ? "" : "disabled"}></label>
          <button type="button" class="btn" id="srv-save" style="align-self:flex-end;margin-bottom:12px" ${srv.local ? "" : "disabled"}>포트 저장</button>
          <span class="hint grow" style="align-self:flex-end;margin-bottom:16px">지금 실행 중: <code>${esc(srv.host)}:${srv.running_port}</code>${srv.configured_port !== srv.running_port ? ` · 다음 실행부터 <b>${srv.configured_port}</b>` : ""}</span>
        </div>
        <p class="hint">바꾼 포트는 다음 실행부터 적용됩니다${srv.desktop ? "(저장 후 바로 다시 시작할 수 있습니다)" : ""}. 저장 위치: <code>${esc(srv.config_path)}</code>${srv.local ? "" : "<br>포트는 서버 PC에서만 바꿀 수 있습니다."}${srv.env_override ? '<br><span class="badge warn">환경 변수 KV_PORT가 설정되어 있어 저장한 포트보다 우선합니다.</span>' : ""}</p>
      </div>
      <div class="panel"><h2>기본 상세화 방식</h2>${modeRadios("default_mode", s.default_mode)}
        <label class="field"><span>LLM에 보내지 않을 섹션 제목 (포함되면 원문 그대로, 한 줄에 하나)</span><textarea name="skip_headings">${esc(s.skip_headings.join("\n"))}</textarea></label>
      </div>
      <div class="panel"><h2>그림 (LLM 모드)</h2>
        <p class="hint">LLM이 그림이 필요한 곳을 고르면, Wikimedia Commons에서 재사용 가능한 라이선스(CC0, 퍼블릭 도메인, CC BY, CC BY-SA)의 이미지를 찾아 LLM이 맞는 것을 고릅니다. 맞는 것이 없으면 LLM이 SVG를 그립니다. 그림은 HTML 안에 들어가고 출처와 라이선스가 캡션에 표시됩니다.</p>
        <div class="row">
          <label class="row"><input type="checkbox" name="images.enabled" ${s.images.enabled ? "checked" : ""}> 그림 넣기</label>
          <label class="row"><input type="checkbox" name="images.web_search" ${s.images.web_search ? "checked" : ""}> 웹 이미지 검색 (인터넷 필요)</label>
          <label class="row"><input type="checkbox" name="images.svg_fallback" ${s.images.svg_fallback ? "checked" : ""}> 없으면 SVG 그리기</label>
        </div>
        <div class="row"><label class="field"><span>섹션당 최대 그림 수</span><input type="number" min="0" max="5" name="images.max_per_section" value="${s.images.max_per_section}"></label>
        <label class="field"><span>웹 이미지 최대 크기(바이트)</span><input type="number" name="images.max_bytes" value="${s.images.max_bytes}"></label></div>
      </div>
      <div class="panel"><h2>Claude Code CLI</h2>
        <p class="hint">이 서버 PC에 설치된 <code>claude</code>를 <code>claude -p</code>로 호출합니다(도구 비활성화). CLI에 로그인된 계정을 그대로 씁니다. 설치되어 있고 로그인되어 있을 때만 생성 방식에 나타납니다.</p>
        <div class="row" style="margin-bottom:6px"><span id="claude-state">${claudeStateHtml(status.claude)}</span><button type="button" class="btn sm" id="claude-recheck">다시 확인</button></div>
        <div class="row"><label class="field grow"><span>실행 파일</span><input type="text" name="claude.command" value="${esc(c.command)}"></label>
        <label class="field grow"><span>모델 (비우면 CLI 기본값, 예: sonnet, opus)</span><input type="text" name="claude.model" value="${esc(c.model)}"></label></div>
        <div class="row"><label class="field"><span>호출당 제한 시간(초)</span><input type="number" name="claude.timeout_s" value="${c.timeout_s}"></label>
        <label class="field"><span>동시 호출 수</span><input type="number" min="1" max="8" name="claude.concurrency" value="${c.concurrency}"></label></div>
        <button type="button" class="btn" id="test-claude">연결 확인</button> <span class="hint" id="test-claude_cli"></span>
      </div>
      <div class="panel"><div class="row"><h2 class="grow" style="margin:0">OpenAI 호환 엔드포인트</h2><button type="button" class="btn sm" id="ep-add">+ 엔드포인트 추가</button></div>
        <p class="hint">vLLM, Ollama, LM Studio 등 <code>/v1/chat/completions</code>를 제공하는 서버를 여러 개 등록할 수 있습니다. 엔드포인트마다 생성 화면에 별도 방식으로 나타납니다. 추가·삭제는 저장해야 반영됩니다.</p>
        <div id="ep-list">${s.openai_endpoints.map(epCard).join("")}</div>
        ${s.openai_endpoints.length ? "" : '<p class="hint" id="ep-empty">등록된 엔드포인트가 없습니다.</p>'}
      </div>
      ${status.desktop ? `<div class="panel"><h2>종료 버튼</h2>
        <label class="field" style="max-width:420px"><span>웹 화면의 종료 버튼을 눌렀을 때</span><select name="exit_action">
          <option value="ask" ${s.exit_action === "ask" ? "selected" : ""}>매번 묻기</option>
          <option value="quit" ${s.exit_action === "quit" ? "selected" : ""}>완전히 종료</option>
          <option value="tray" ${s.exit_action === "tray" ? "selected" : ""}>트레이에 두기 (서버 계속 실행)</option></select></label></div>` : ""}
      <div class="row"><button type="submit" class="btn primary">저장</button>${status.auth_required ? '<button type="button" class="btn" id="logout">관리 토큰 지우기</button>' : ""}<span class="hint">KnowledgeView ${esc(status.version)}</span></div>
      </form>`;

    const collect = () => {
      const f = $("#sf"), patch = { claude: {}, images: {} };
      const lines = (v) => v.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
      patch.source_roots = lines(f.source_roots.value);
      patch.skip_headings = lines(f.skip_headings.value);
      patch.output_dir = f.output_dir.value.trim() || "output";
      patch.default_mode = $$("input[name=default_mode]").find((i) => i.checked)?.value || "none";
      patch.history_keep = Number(f.history_keep.value) || 0;
      if (f.exit_action) patch.exit_action = f.exit_action.value;
      patch.openai_endpoints = $$(".ep", f).map((card) => {
        const ep = { id: card.dataset.id || "" };
        $$("[data-k]", card).forEach((el) => { ep[el.dataset.k] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value.trim(); });
        return ep;
      });
      $$("[name^='claude.'],[name^='images.']", f).forEach((el) => {
        const [grp, key] = el.name.split(".");
        patch[grp][key] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value.trim();
      });
      return patch;
    };
    $("#sf").onsubmit = async (e) => { e.preventDefault(); try { await api("PUT", "/api/settings", collect()); toast("저장했습니다."); renderSettings(); } catch (err) { toast(err.message, true); } };
    $("#srv-save").onclick = async () => {
      const port = Number($("#srv-port").value);
      try {
        const r = await api("PUT", "/api/server", { port });
        if (!r.restart_required) { toast("저장했습니다. 지금 실행 중인 포트와 같습니다."); return renderSettings(); }
        if (srv.desktop && srv.local && await ask("포트 변경", `<p>포트를 <b>${port}</b>로 저장했습니다. 지금 다시 시작해서 적용할까요?</p><p class="hint">진행 중인 작업은 취소됩니다. 새 주소: <code>http://127.0.0.1:${port}/</code></p>`, "지금 다시 시작")) {
          await api("POST", "/api/app/restart");
          return showRestarting(port);
        }
        toast(`저장했습니다. 다음 실행부터 포트 ${port}를 씁니다.`);
        renderSettings();
      } catch (err) { toast(err.message, true); }
    };
    $("#claude-recheck").onclick = async () => {
      $("#claude-state").textContent = "확인 중…";
      try {
        const st = await api("POST", "/api/claude/check");
        await loadStatus();
        $("#claude-state").innerHTML = claudeStateHtml(st);
        toast("다시 확인했습니다. 생성 방식 목록에 반영됩니다.");
      } catch (err) { $("#claude-state").textContent = "실패: " + err.message; }
    };
    $("#test-claude").onclick = async () => {
      const out = $("#test-claude_cli");
      out.textContent = "확인 중…";
      try {
        const r = await api("POST", "/api/llm/test", { mode: "claude_cli" });
        out.textContent = `정상: "${r.reply}" ${r.model ? "(" + r.model + ")" : ""}`;
      } catch (err) { out.textContent = "실패: " + err.message; }
    };
    const bindCard = (card) => {
      $(".ep-del", card).onclick = () => { card.remove(); toast("저장하면 삭제됩니다."); };
      $(".ep-test", card).onclick = async () => {
        const out = $(".ep-out", card), idx = $$(".ep").indexOf(card);
        out.textContent = "저장 후 확인 중…";
        try {
          const saved = await api("PUT", "/api/settings", collect());
          // New cards got ids on save; keep them so the next save does not duplicate them.
          $$(".ep").forEach((c, n) => { if (saved.openai_endpoints[n]) c.dataset.id = saved.openai_endpoints[n].id; });
          const ep = saved.openai_endpoints[idx];
          const r = await api("POST", "/api/llm/test", { mode: "openai:" + ep.id });
          out.textContent = `정상: "${r.reply}" (${r.model})`;
        } catch (err) { out.textContent = "실패: " + err.message; }
      };
    };
    $$(".ep").forEach(bindCard);
    $("#ep-add").onclick = () => {
      const tmp = document.createElement("div");
      tmp.innerHTML = epCard({ id: "", name: "", base_url: "", model: "", temperature: 0.3, max_tokens: 8192, timeout_s: 900, concurrency: 2, reasoning_effort: "", has_api_key: false });
      const card = tmp.firstElementChild;
      $("#ep-list").appendChild(card);
      const empty = $("#ep-empty"); if (empty) empty.remove();
      bindCard(card);
      $("[data-k=name]", card).focus();
    };
    const lo = $("#logout"); if (lo) lo.onclick = () => { localStorage.removeItem("kv-token"); toast("토큰을 지웠습니다."); };
  }

  // ---------------------------------------------------------------- router
  function currentTab() { return (location.hash.match(/^#\/(\w+)/) || [, "library"])[1]; }
  async function route() {
    const tab = currentTab();
    $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
    try {
      if (tab === "generate") await renderGenerate();
      else if (tab === "jobs") await renderJobs();
      else if (tab === "settings") await renderSettings();
      else {
        const m = location.hash.match(/^#\/library\/(.*)$/);
        await renderLibrary(m ? decodeURIComponent(m[1]) : undefined);
      }
    } catch (e) { view.innerHTML = `<div class="panel empty">${esc(e.message)}</div>`; }
  }

  // ---------------------------------------------------------------- exit (desktop app)
  function showClosed(msg) {
    clearTimeout(pollTimer);
    document.body.innerHTML = `<main><div class="panel empty" style="margin-top:15vh"><h2>${esc(msg)}</h2><p class="hint">이 브라우저 탭은 닫아도 됩니다.</p></div></main>`;
    setTimeout(() => { try { window.close(); } catch (e) {} }, 400);
  }
  // After a restart on a new port: wait until the new server answers, then go there.
  function showRestarting(port) {
    clearTimeout(pollTimer);
    const url = `${location.protocol}//${location.hostname}:${port}/#/settings`;
    document.body.innerHTML = `<main><div class="panel empty" style="margin-top:15vh"><h2>포트 ${port}로 다시 시작하는 중…</h2>
      <p class="hint">준비되면 자동으로 이동합니다(처음 시작에 30초 가까이 걸릴 수 있음). 이동하지 않으면 <a href="${esc(url)}">${esc(url)}</a>를 여세요.</p></div></main>`;
    const started = Date.now();
    const tryNew = () => fetch(`${location.protocol}//${location.hostname}:${port}/api/status`, { mode: "no-cors", cache: "no-store" })
      .then(() => { location.href = url; })
      .catch(() => { if (Date.now() - started < 120000) setTimeout(tryNew, 1500); });
    setTimeout(tryNew, 2500);
  }
  async function doExit(action, remember) {
    await api("POST", "/api/app/exit", { action, remember });
    showClosed(action === "quit" ? "KnowledgeView를 종료했습니다." : "KnowledgeView는 트레이에서 계속 실행 중입니다. 트레이 아이콘이나 시작 메뉴로 다시 열 수 있습니다.");
  }
  $("#quit").onclick = async () => {
    try {
      await loadStatus();
      const { jobs } = await api("GET", "/api/jobs");
      const active = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
      const warn = active ? `<p class="badge warn" style="display:inline-block">진행 중이거나 대기 중인 작업 ${active}개는 완전히 종료하면 취소됩니다.</p>` : "";
      if (status.exit_action === "tray") return doExit("tray", false);
      if (status.exit_action === "quit") {
        if (active && !(await ask("종료", warn + "<p>KnowledgeView를 완전히 종료할까요?</p>", "종료"))) return;
        return doExit("quit", false);
      }
      const r = await ask("KnowledgeView 종료", `${warn}
        <label class="mode" style="margin:10px 0"><input type="radio" name="exit" value="tray" checked><span><b>트레이에 두기</b><small>서버는 계속 실행하고 이 화면만 닫습니다. 다른 PC 접속과 진행 중인 작업이 계속됩니다.</small></span></label>
        <label class="mode"><input type="radio" name="exit" value="quit"><span><b>완전히 종료</b><small>서버와 트레이 아이콘을 모두 끕니다.</small></span></label>
        <label class="row" style="margin-top:12px"><input type="checkbox" name="remember"> 다음부터 묻지 않기 (설정에서 바꿀 수 있음)</label>`, "확인");
      if (!r) return;
      const action = $$('#dlg input[name=exit]').find((i) => i.checked).value;
      await doExit(action, !!r.remember);
    } catch (e) { toast(e.message, true); }
  };

  $("#theme").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; try { localStorage.setItem("kv-theme", t); } catch (e) {} };
  addEventListener("hashchange", () => { route(); if (currentTab() === "jobs") pollJobs(); });
  loadStatus().then((st) => { $("#quit").hidden = !(st.desktop && st.local); route(); }, (e) => toast(e.message, true));
  pollJobs();
})();
