/* KnowledgeView page runtime, served by the app at /assets/kv-page.js.
 *
 * Pages with <meta name="kv-runtime"> leave diagram rendering to this script
 * (mermaid render, re-render on theme change, zoom/source tools). Older pages
 * render diagrams inline; for them this script only fixes text contrast.
 * The contrast fix runs on every rendered mermaid SVG: each label's effective
 * background is found (HTML label background, else the topmost filled shape
 * under the label's centre, else the diagram background) and the text colour is
 * switched to near-black or white when the WCAG contrast ratio is below 4.5.
 */
(function () {
  "use strict";
  if (window.KV_RUNTIME) return;
  window.KV_RUNTIME = 2;

  var root = document.documentElement;
  var managed = !!document.querySelector('meta[name="kv-runtime"]');
  var DARK = { r: 17, g: 24, b: 39, a: 1 }, LIGHT = { r: 255, g: 255, b: 255, a: 1 };
  var MIN_RATIO = 4.5;
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  // ------------------------------------------------------------------ colour math
  function parseColor(s) {
    if (!s || s === "none" || s === "transparent" || s.indexOf("url(") === 0) return null;
    var m = s.match(/rgba?\(([^)]+)\)/);
    if (m) {
      var p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
      if (p.length < 3) return null;
      var a = p.length > 3 ? p[3] : 1;
      if (String(m[1]).indexOf("%") > -1 && p.length > 3 && p[3] > 1) a = p[3] / 100;
      return { r: p[0], g: p[1], b: p[2], a: a };
    }
    m = s.match(/^#([0-9a-f]{3,8})$/i);
    if (m) {
      var h = m[1];
      if (h.length <= 4) h = h.split("").map(function (c) { return c + c; }).join("");
      return { r: parseInt(h.substr(0, 2), 16), g: parseInt(h.substr(2, 2), 16), b: parseInt(h.substr(4, 2), 16), a: h.length === 8 ? parseInt(h.substr(6, 2), 16) / 255 : 1 };
    }
    return null;
  }
  function blend(fg, bg) {
    var a = fg.a;
    return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
  }
  function lum(c) {
    var f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function ratio(a, b) {
    var x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  }
  function css(c) { return "rgb(" + Math.round(c.r) + "," + Math.round(c.g) + "," + Math.round(c.b) + ")"; }

  function containerBg(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentNode) {
      var c = parseColor(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.5) return blend(c, LIGHT);
    }
    return root.dataset.theme === "dark" ? { r: 23, g: 27, b: 35, a: 1 } : LIGHT;
  }

  // Filled shapes in paint order, with their screen boxes.
  function filledShapes(svg, base) {
    var out = [];
    $$("rect,polygon,circle,ellipse,path", svg).forEach(function (s) {
      if (s.closest("marker,defs,clipPath,mask,pattern")) return;
      var st = getComputedStyle(s);
      if (st.display === "none" || st.visibility === "hidden") return;
      var c = parseColor(st.fill);
      if (!c) return;
      var op = parseFloat(st.fillOpacity || "1") * parseFloat(st.opacity || "1");
      c.a *= isNaN(op) ? 1 : op;
      if (c.a < 0.05) return;
      var r = s.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      out.push({ rect: r, color: c });
    });
    return out;
  }

  function backgroundAt(el, shapes, base) {
    // HTML labels (foreignObject) may carry their own background, e.g. edge labels.
    for (var n = el; n && n.nodeType === 1 && n.tagName.toLowerCase() !== "foreignobject"; n = n.parentNode) {
      var hb = parseColor(getComputedStyle(n).backgroundColor);
      if (hb && hb.a >= 0.5) return blend(hb, base);
    }
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    var x = r.left + r.width / 2, y = r.top + r.height / 2;
    var bg = base;
    // Composite every shape under the centre in paint order (later shapes are on top).
    shapes.forEach(function (s) {
      var b = s.rect;
      if (x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) bg = blend(s.color, bg);
    });
    return bg;
  }

  function textElements(svg) {
    var els = $$("text", svg);
    $$("foreignObject *", svg).forEach(function (n) {
      for (var c = n.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3 && c.nodeValue.trim()) { els.push(n); break; }
      }
    });
    return els;
  }

  function fixContrast(svg) {
    if (!svg || !svg.isConnected) return;
    var box = svg.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return; // hidden: retry later
    var base = containerBg(svg.parentNode);
    var shapes = filledShapes(svg, base);
    textElements(svg).forEach(function (el) {
      var isSvgText = el instanceof SVGElement;
      var st = getComputedStyle(el);
      var fg = parseColor(isSvgText ? st.fill : st.color);
      if (!fg) return;
      var bg = backgroundAt(el, shapes, base);
      if (!bg) return;
      fg = blend(fg, bg);
      stats.checked++;
      if (ratio(fg, bg) >= MIN_RATIO) return;
      var pick = ratio(DARK, bg) >= ratio(LIGHT, bg) ? DARK : LIGHT;
      stats.fixed++;
      if (ratio(pick, bg) < MIN_RATIO) stats.low++;
      if (isSvgText) {
        el.style.setProperty("fill", css(pick), "important");
        $$("tspan", el).forEach(function (t) { t.style.setProperty("fill", css(pick), "important"); });
      } else {
        el.style.setProperty("color", css(pick), "important");
      }
    });
    svg.setAttribute("data-kv-contrast", "1");
    // Counters for checks (e.g. headless --dump-dom): checked labels, recoloured, still below 4.5.
    root.setAttribute("data-kv-contrast", stats.checked + "/" + stats.fixed + "/" + stats.low);
  }
  var stats = { checked: 0, fixed: 0, low: 0 };

  function fixAll() {
    $$(".kv-diagram pre.mermaid svg").forEach(function (svg) {
      if (svg.getAttribute("data-kv-contrast") !== "1") fixContrast(svg);
    });
  }
  var pending = false;
  function scheduleFix() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () { pending = false; fixAll(); });
  }

  // Re-check whenever a diagram's SVG is (re)inserted, and when hidden content is shown.
  function observeDiagrams() {
    var mo = new MutationObserver(scheduleFix);
    $$(".kv-diagram pre.mermaid").forEach(function (n) { mo.observe(n, { childList: true }); });
    document.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("#kv-toggle-added,.kv-src,details,summary")) setTimeout(fixAll, 50);
    });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { setTimeout(fixAll, 50); });
  }

  // ------------------------------------------------------------------ managed rendering
  function renderDiagrams() {
    if (!window.mermaid) return;
    var dark = root.dataset.theme === "dark";
    mermaid.initialize({
      startOnLoad: false, theme: dark ? "dark" : "default", securityLevel: "strict",
      fontFamily: getComputedStyle(document.body).fontFamily, flowchart: { htmlLabels: true }
    });
    var nodes = $$(".kv-diagram pre.mermaid");
    nodes.forEach(function (n) {
      var src = n.parentNode.querySelector(".kv-diagram-src");
      n.removeAttribute("data-processed");
      n.textContent = src ? src.textContent : n.textContent;
      var e = n.parentNode.querySelector(".kv-diagram-error"); if (e) e.remove();
    });
    return mermaid.run({ nodes: nodes, suppressErrors: true }).catch(function () {}).then(function () {
      nodes.forEach(function (n) {
        var svg = n.querySelector("svg");
        if (!svg || n.querySelector('svg[aria-roledescription="error"]')) {
          var d = document.createElement("div");
          d.className = "kv-diagram-error";
          d.textContent = "다이어그램을 그리지 못했습니다. </> 버튼으로 원본을 볼 수 있습니다.";
          n.parentNode.appendChild(d);
        }
      });
      fixAll();
    });
  }

  // ------------------------------------------------------------------ viewer (zoom / pan / save)
  // Full-screen viewer for diagrams and figures. Zooming re-sizes the SVG itself
  // (width/height) instead of a CSS scale, so text stays sharp at any zoom.
  var V = null;

  function slugName(text) {
    return (String(text || "").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "").replace(/\s+/g, "_").replace(/^[._]+|[._]+$/g, "").slice(0, 50)) || "diagram";
  }

  function nearestHeading(el) {
    var hs = $$(".kv-body h2[id], .kv-body h3[id]"), last = null;
    for (var i = 0; i < hs.length; i++) {
      if (hs[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) last = hs[i]; else break;
    }
    return last ? last.textContent.replace(/^#\s*/, "").trim() : "";
  }

  function buildViewer() {
    var css = document.createElement("style");
    css.textContent = [
      "#kv-viewer{position:fixed;inset:0;z-index:60;background:rgba(10,12,16,.85);display:none;flex-direction:column}",
      "#kv-viewer.open{display:flex}",
      "#kv-viewer .kvv-bar{display:flex;gap:6px;align-items:center;padding:8px 12px;background:var(--surface,#fff);color:var(--text,#111);border-bottom:1px solid var(--border,#ddd);flex-wrap:wrap}",
      "#kv-viewer .kvv-bar button{border:1px solid var(--border,#ccc);background:var(--surface,#fff);color:inherit;border-radius:8px;padding:4px 10px;font:inherit;font-size:13px;cursor:pointer;line-height:1.4}",
      "#kv-viewer .kvv-bar button:hover{border-color:var(--accent,#2f6fde);color:var(--accent,#2f6fde)}",
      "#kv-viewer .kvv-title{flex:1;min-width:120px;font-size:13px;opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      "#kv-viewer .kvv-zoom{min-width:56px;text-align:center;font-size:13px;font-variant-numeric:tabular-nums}",
      "#kv-viewer .kvv-msg{font-size:12px;color:var(--c-warning,#c77700)}",
      "#kv-viewer .kvv-stage{flex:1;position:relative;overflow:hidden;cursor:grab;touch-action:none;user-select:none}",
      "#kv-viewer .kvv-stage.drag{cursor:grabbing}",
      "#kv-viewer .kvv-item{position:absolute;left:0;top:0;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.4);padding:12px}",
      "#kv-viewer .kvv-item svg,#kv-viewer .kvv-item img{display:block;max-width:none!important;max-height:none!important;-webkit-user-drag:none;pointer-events:none}",
      "#kv-viewer .kvv-hint{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);color:#fff;font-size:12px;opacity:.75;pointer-events:none;white-space:nowrap}",
      ".kv-diagram pre.mermaid svg,.kv-figure img{cursor:zoom-in}"
    ].join("\n");
    document.head.appendChild(css);
    var v = document.createElement("div");
    v.id = "kv-viewer";
    v.setAttribute("role", "dialog");
    v.innerHTML = '<div class="kvv-bar"><span class="kvv-title"></span><span class="kvv-msg"></span>' +
      '<button type="button" data-a="out" title="축소 (-)">−</button><span class="kvv-zoom">100%</span>' +
      '<button type="button" data-a="in" title="확대 (+)">+</button>' +
      '<button type="button" data-a="fit" title="화면에 맞춤 (0)">맞춤</button>' +
      '<button type="button" data-a="one" title="실제 크기 (1)">1:1</button>' +
      '<button type="button" data-a="svg">SVG 저장</button>' +
      '<button type="button" data-a="png">PNG 저장</button>' +
      '<button type="button" data-a="close" title="닫기 (Esc)">닫기 ✕</button></div>' +
      '<div class="kvv-stage"><div class="kvv-item"></div>' +
      '<div class="kvv-hint">휠: 확대/축소 · 드래그: 이동 · 더블클릭: 2배/맞춤 · 키보드 + − 0 1 방향키</div></div>';
    document.body.appendChild(v);

    V = { root: v, stage: v.querySelector(".kvv-stage"), item: v.querySelector(".kvv-item"),
          zoomLabel: v.querySelector(".kvv-zoom"), title: v.querySelector(".kvv-title"), msg: v.querySelector(".kvv-msg"),
          el: null, kind: "", w: 1, h: 1, s: 1, tx: 0, ty: 0, name: "", bg: null, pointers: {}, pinch: null };

    v.querySelector(".kvv-bar").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-a]");
      if (!b) return;
      var a = b.getAttribute("data-a"), r = V.stage.getBoundingClientRect();
      if (a === "in") zoomAt(1.25, r.width / 2, r.height / 2);
      else if (a === "out") zoomAt(0.8, r.width / 2, r.height / 2);
      else if (a === "fit") fit();
      else if (a === "one") setScale(1);
      else if (a === "svg") saveSvg();
      else if (a === "png") savePng();
      else if (a === "close") closeViewer();
    });
    V.stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = V.stage.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    V.stage.addEventListener("pointerdown", function (e) {
      V.stage.setPointerCapture(e.pointerId);
      V.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      V.stage.classList.add("drag");
      V.pinch = null;
    });
    V.stage.addEventListener("pointermove", function (e) {
      var p = V.pointers[e.pointerId];
      if (!p) return;
      var ids = Object.keys(V.pointers);
      if (ids.length === 1) {
        V.tx += e.clientX - p.x; V.ty += e.clientY - p.y;
        p.x = e.clientX; p.y = e.clientY;
        apply();
      } else if (ids.length === 2) {
        p.x = e.clientX; p.y = e.clientY;
        var a = V.pointers[ids[0]], b = V.pointers[ids[1]], r = V.stage.getBoundingClientRect();
        var d = Math.hypot(a.x - b.x, a.y - b.y), cx = (a.x + b.x) / 2 - r.left, cy = (a.y + b.y) / 2 - r.top;
        if (V.pinch) { zoomAt(d / V.pinch.d, cx, cy); V.tx += cx - V.pinch.cx; V.ty += cy - V.pinch.cy; apply(); }
        V.pinch = { d: d, cx: cx, cy: cy };
      }
    });
    function up(e) {
      delete V.pointers[e.pointerId];
      V.pinch = null;
      if (!Object.keys(V.pointers).length) V.stage.classList.remove("drag");
    }
    V.stage.addEventListener("pointerup", up);
    V.stage.addEventListener("pointercancel", up);
    V.stage.addEventListener("dblclick", function (e) {
      var r = V.stage.getBoundingClientRect();
      if (V.s < fitScale() * 1.5) zoomAt(2, e.clientX - r.left, e.clientY - r.top); else fit();
    });
    document.addEventListener("keydown", function (e) {
      if (!V.root.classList.contains("open")) return;
      var r = V.stage.getBoundingClientRect(), k = e.key;
      if (k === "Escape") closeViewer();
      else if (k === "+" || k === "=") zoomAt(1.25, r.width / 2, r.height / 2);
      else if (k === "-" || k === "_") zoomAt(0.8, r.width / 2, r.height / 2);
      else if (k === "0") fit();
      else if (k === "1") setScale(1);
      else if (k === "ArrowLeft") { V.tx += 80; apply(); }
      else if (k === "ArrowRight") { V.tx -= 80; apply(); }
      else if (k === "ArrowUp") { V.ty += 80; apply(); }
      else if (k === "ArrowDown") { V.ty -= 80; apply(); }
      else return;
      e.preventDefault();
    });
    window.addEventListener("resize", function () { if (V.root.classList.contains("open")) fit(); });
  }

  function apply() {
    var w = V.w * V.s, h = V.h * V.s;
    if (V.kind === "svg") { V.el.setAttribute("width", w); V.el.setAttribute("height", h); }
    else { V.el.style.width = w + "px"; V.el.style.height = h + "px"; }
    V.item.style.transform = "translate(" + Math.round(V.tx) + "px," + Math.round(V.ty) + "px)";
    V.zoomLabel.textContent = Math.round(V.s * 100) + "%";
  }
  function fitScale() {
    var r = V.stage.getBoundingClientRect();
    return Math.max(0.02, Math.min((r.width - 64) / V.w, (r.height - 64) / V.h));
  }
  function center() {
    var r = V.stage.getBoundingClientRect();
    V.tx = (r.width - V.w * V.s - 24) / 2;
    V.ty = (r.height - V.h * V.s - 24) / 2;
  }
  function fit() { V.s = fitScale(); center(); apply(); }
  function setScale(s) { V.s = s; center(); apply(); }
  function zoomAt(f, cx, cy) {
    var ns = Math.min(20, Math.max(0.02, V.s * f));
    // Keep the point under the cursor still (12px = item padding).
    V.tx = cx - (cx - V.tx - 12) * (ns / V.s) - 12;
    V.ty = cy - (cy - V.ty - 12) * (ns / V.s) - 12;
    V.s = ns;
    apply();
  }

  function openViewer(source, title, name) {
    if (!V) buildViewer();
    V.msg.textContent = "";
    V.item.innerHTML = "";
    var isSvg = source.tagName.toLowerCase() === "svg";
    var el = source.cloneNode(true);
    V.kind = isSvg ? "svg" : "img";
    if (isSvg) {
      var vb = (source.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
      var box = source.getBoundingClientRect();
      V.w = vb.length === 4 && vb[2] > 0 ? vb[2] : (box.width || 800);
      V.h = vb.length === 4 && vb[3] > 0 ? vb[3] : (box.height || 600);
      el.style.maxWidth = "none";
      el.removeAttribute("style");
      V.bg = containerBg(source.parentNode);
    } else {
      V.w = source.naturalWidth || source.width || 800;
      V.h = source.naturalHeight || source.height || 600;
      el.removeAttribute("style");
      el.removeAttribute("loading");
      V.bg = LIGHT;
    }
    V.item.style.background = css(V.bg);
    V.el = el;
    V.item.appendChild(el);
    V.title.textContent = title || "";
    V.name = slugName(name || title);
    V.source = source;
    V.root.classList.add("open");
    fit();
  }
  function closeViewer() { if (V) { V.root.classList.remove("open"); V.item.innerHTML = ""; } }

  function download(blob, filename) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function svgText() {
    var c = V.el.cloneNode(true);
    c.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    c.setAttribute("width", V.w);
    c.setAttribute("height", V.h);
    c.setAttribute("style", "background-color:" + css(V.bg));
    return new XMLSerializer().serializeToString(c);
  }
  function dataUriParts(src) {
    var m = /^data:([^;,]+);base64,(.*)$/.exec(src || "");
    return m ? { mime: m[1], data: m[2] } : null;
  }
  function b64Blob(b64, mime) {
    var bin = atob(b64), arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime });
  }
  function saveSvg() {
    if (V.kind === "svg") return download(new Blob([svgText()], { type: "image/svg+xml" }), V.name + ".svg");
    var p = dataUriParts(V.source.src);
    if (p && p.mime === "image/svg+xml") return download(b64Blob(p.data, p.mime), V.name + ".svg");
    V.msg.textContent = "이 그림은 SVG가 아닙니다. PNG 저장을 쓰세요.";
  }
  function savePng() {
    var p = V.kind === "img" ? dataUriParts(V.source.src) : null;
    if (p && p.mime === "image/png") return download(b64Blob(p.data, p.mime), V.name + ".png");
    var src = V.kind === "svg" ? "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgText()) : V.source.src;
    var img = new Image();
    img.onload = function () {
      var scale = Math.min(4, Math.max(2, 2400 / Math.max(V.w, V.h)));
      var cv = document.createElement("canvas");
      cv.width = Math.round(V.w * scale); cv.height = Math.round(V.h * scale);
      var ctx = cv.getContext("2d");
      ctx.fillStyle = css(V.bg); ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.drawImage(img, 0, 0, cv.width, cv.height);
      try {
        cv.toBlob(function (b) {
          if (b) download(b, V.name + ".png"); else V.msg.textContent = "PNG로 바꾸지 못했습니다. SVG 저장을 쓰세요.";
        }, "image/png");
      } catch (err) {
        V.msg.textContent = "브라우저 보안 제한으로 이 다이어그램은 PNG로 바꿀 수 없습니다. SVG 저장을 쓰세요.";
      }
    };
    img.onerror = function () { V.msg.textContent = "PNG로 바꾸지 못했습니다. SVG 저장을 쓰세요."; };
    img.src = src;
  }

  function diagramOpener(f) {
    return function () {
      var svg = f.querySelector("pre.mermaid svg");
      if (!svg) return;
      var idx = $$(".kv-diagram").indexOf(f) + 1;
      var head = nearestHeading(f);
      openViewer(svg, (head ? head + " · " : "") + "다이어그램 " + idx, (idx < 10 ? "0" : "") + idx + "_" + (head || "diagram"));
    };
  }

  function setupTools() {
    $$(".kv-diagram").forEach(function (f) {
      var src = f.querySelector(".kv-src"), zoom = f.querySelector(".kv-zoom");
      if (src) src.onclick = function () { var s = f.querySelector(".kv-diagram-src"); s.hidden = !s.hidden; };
      var open = diagramOpener(f);
      if (zoom) { zoom.onclick = open; zoom.title = "확대해서 보기"; }
      f.querySelector("pre.mermaid").addEventListener("click", function (e) { if (e.target.closest("svg")) open(); });
    });
    $$(".kv-figure img").forEach(function (img) {
      img.onclick = function () {
        var cap = img.closest("figure").querySelector("figcaption");
        var text = cap ? (cap.firstChild && cap.firstChild.nodeType === 3 ? cap.firstChild.nodeValue : cap.textContent) : "";
        openViewer(img, text, text);
      };
    });
  }

  function addExportButton() {
    if (location.pathname.indexOf("/view/") !== 0) return;
    if (!document.querySelector(".kv-figure, .kv-diagram")) return;
    var anchor = document.getElementById("kv-theme");
    if (!anchor || document.getElementById("kv-export")) return;
    var rel = decodeURIComponent(location.pathname.slice("/view/".length));
    var a = document.createElement("a");
    a.id = "kv-export";
    a.className = "kv-btn";
    a.href = "/api/pages/figures.zip?rel=" + encodeURIComponent(rel);
    a.title = "이 페이지의 그림(PNG/SVG)과 다이어그램 원본을 ZIP으로 받기";
    a.textContent = "그림 내보내기";
    anchor.parentNode.insertBefore(a, anchor);
  }

  function start() {
    observeDiagrams();
    setupTools();
    addExportButton();
    if (managed) {
      renderDiagrams();
      new MutationObserver(function (m) {
        if (m.some(function (r) { return r.attributeName === "data-theme"; })) renderDiagrams();
      }).observe(root, { attributes: true });
    } else {
      scheduleFix();
    }
  }

  window.KV = { fixContrast: fixContrast, fixAll: fixAll, ratio: ratio, parseColor: parseColor, render: renderDiagrams, open: openViewer };
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start);
})();
