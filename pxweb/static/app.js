/* pxkit web —— 前端逻辑。只做 UI，算法全在后端 pxkit。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const api = async (path, body) => {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({ error: "响应不是 JSON" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  };

  // ---------------------------------------------------------------- 状态
  const S = {
    key: null,      // 当前图
    origKey: null,  // 原图
    w: 0, h: 0,
    block: 1,
    zoom: 1,
    tiles: [],      // [{key,name}]
    busy: false,
  };

  // ---------------------------------------------------------------- 日志
  let logN = 0;
  function log(msg, kind) {
    const body = $("logBody");
    const ln = document.createElement("div");
    ln.className = "ln" + (kind ? " " + kind : "");
    const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    ln.innerHTML = '<span class="t">' + t + "</span>";
    ln.appendChild(document.createTextNode(msg));
    body.appendChild(ln);
    body.scrollTop = body.scrollHeight;
    logN += 1;
    $("logCount").textContent = String(logN);
    const dot = $("logDot");
    dot.className = "dot" + (kind === "err" ? " err" : kind === "ok" ? " ok" : "");
  }

  function setBusy(on, label) {
    S.busy = on;
    document.body.style.cursor = on ? "progress" : "";
    $("bannerHint").textContent = on ? (label || "处理中…") : "就绪";
  }

  // ---------------------------------------------------------------- 画布
  function srcOf(key) {
    return "/work/" + key + ".png?t=" + Date.now();
  }

  function renderCanvas() {
    const img = $("canvasImg");
    const empty = $("canvasEmpty");
    if (!S.key) {
      img.classList.add("sr-only");
      empty.style.display = "";
      return;
    }
    empty.style.display = "none";
    img.classList.remove("sr-only");
    img.src = srcOf(S.key);
    img.alt = "当前图像 " + S.w + "×" + S.h;
    applyZoom();
    updateGridOverlay();
  }

  function applyZoom() {
    const img = $("canvasImg");
    if (!S.key) return;
    img.style.width = Math.round(S.w * S.zoom) + "px";
    img.style.height = "auto";
    $("zoomVal").textContent = Math.round(S.zoom * 100) + "%";
    updateGridOverlay();
  }

  function updateGridOverlay() {
    const ov = $("gridOverlay");
    const on = $("chkGrid").checked && S.key;
    ov.classList.toggle("on", !!on);
    if (!on) return;
    const cell = Math.max(1, S.w) / Math.max(1, Number($("inCols").value) || 4);
    ov.style.setProperty("--cell", Math.round(cell * S.zoom) + "px");
  }

  function setMeta() {
    $("mSize").textContent = S.key ? S.w + " × " + S.h : "—";
    $("mBlock").textContent = S.key ? "×" + S.block : "—";
    $("vSize").textContent = S.key ? Math.max(S.w, S.h) + "px" : "—";
  }

  // ---------------------------------------------------------------- 导入
  async function openFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) { log("不是图片：" + file.name, "err"); return; }
    setBusy(true, "导入中…");
    try {
      const data = await new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = () => rej(new Error("读取文件失败"));
        fr.readAsDataURL(file);
      });
      const j = await api("/api/open", { data });
      S.key = j.key; S.origKey = j.key;
      S.w = j.w; S.h = j.h; S.block = j.block || 1;
      S.tiles = [];
      S.zoom = fitZoom();
      renderCanvas(); setMeta(); clearResult();
      log(`导入 ${file.name} — ${j.w}×${j.h}，检测到 ×${j.block}（置信 ${j.score}）`, "ok");
      $("bannerText").textContent =
        `${file.name} · ${j.w}×${j.h} · 像素倍数 ×${j.block}`;
      $("bannerHint").textContent = "就绪";
      // 槽位里显示原图缩略（点它可重新选图）
      $("refThumb").src = srcOf(j.key);
      $("refThumb").hidden = false;
      $("dzPrompt").hidden = true;
      // 补一次只读探测：四角背景色 + 颜色数
      await refreshMeta();
      if (!S.bgTouched) {
        log(`  背景 ${$("bgColor").value}，${$("mColors").textContent} 色`);
      } else {
        log(`  ${$("mColors").textContent} 色`);
      }
    } catch (e) {
      log(String(e.message || e), "err");
      $("bannerHint").textContent = "出错";
    } finally {
      setBusy(false);
    }
  }

  function fitZoom() {
    const shell = $("canvasShell");
    const availW = Math.max(64, shell.clientWidth - 40);
    const availH = Math.max(64, shell.clientHeight - 40);
    if (!S.w || !S.h) return 1;
    const z = Math.min(availW / S.w, availH / S.h);
    return Math.max(0.05, Math.min(16, z));
  }

  function clearResult() {
    $("resultBody").innerHTML = '<div class="result-empty">暂无结果</div>';
    $("resultInfo").textContent = "空";
    $("resultCount").textContent = "";
  }

  function showResultFiles(items, label) {
    const body = $("resultBody");
    body.innerHTML = "";
    if (!items.length) { clearResult(); return; }
    items.forEach((it, i) => {
      const d = document.createElement("div");
      d.className = "tile";
      d.title = (it.name || ("#" + i)) + `  ${it.w}×${it.h}`;
      const im = document.createElement("img");
      im.src = srcOf(it.key);
      im.alt = it.name || ("tile " + i);
      const idx = document.createElement("span");
      idx.className = "idx";
      idx.textContent = String(i).padStart(2, "0");
      d.appendChild(im); d.appendChild(idx);
      d.addEventListener("click", () => {
        S.key = it.key; S.w = it.w; S.h = it.h;
        renderCanvas(); setMeta();
        log(`切换到 ${it.name || ("#" + i)}  ${it.w}×${it.h}`);
      });
      body.appendChild(d);
    });
    $("resultInfo").textContent = label;
    $("resultCount").textContent = items.length + " 项";
  }

  // ---------------------------------------------------------------- 操作
  async function run(op, params, label) {
    if (!S.key) { log("先上传一张图片", "err"); return null; }
    if (S.busy) return null;
    setBusy(true, label + "…");
    try {
      const j = await api("/api/op", { op, key: S.key, params: params || {} });
      log(`${label} — ${j.w}×${j.h}` + (j.same ? "（未改图）" : ""), "ok");
      return j;
    } catch (e) {
      log(`${label} 失败：${e.message || e}`, "err");
      $("bannerHint").textContent = "出错";
      return null;
    } finally {
      setBusy(false);
    }
  }

  function adopt(j, label) {
    if (!j) return;
    S.key = j.key; S.w = j.w; S.h = j.h;
    S.zoom = fitZoom();
    renderCanvas(); setMeta();
    $("bannerText").textContent = `${label} · ${j.w}×${j.h}`;
    // 结果区镜像当前图，避免显示上一个操作留下的旧图
    showResultFiles([{ key: j.key, w: j.w, h: j.h, name: label }], label);
    refreshMeta();                       // 异步补颜色 / 四角背景，不阻塞操作
  }

  // 只读探测：颜色数 + 四角背景色。用代次号防止旧请求覆盖新状态。
  let _metaSeq = 0;
  async function refreshMeta() {
    if (!S.key) return;
    const seq = ++_metaSeq;
    const key = S.key;
    try {
      const pr = await api("/api/op", { op: "probe", key, params: {} });
      if (seq !== _metaSeq || S.key !== key) return;
      $("mColors").textContent = String(pr.info.color_count);
      if (!S.bgTouched && pr.info.bg_alpha >= 128) {
        setPicker(pr.info.bg_corner || [0, 0, 0]);
      }
    } catch (_) { /* 探测失败不阻断 */ }
  }

  // 检测
  async function doDetect() {
    const j = await run("detect", {}, "检测倍数");
    if (!j) return;
    S.block = j.info.block;
    const cands = j.info.candidates || {};
    const top = Object.entries(cands)
      .sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([k, v]) => `×${k}:${v}`).join("  ");
    log(`块大小 = ${S.block}（score ${j.info.score}）  候选 ${top}`, "ok");
    setMeta();
  }

  // 去像素化
  async function doDepixel() {
    const j = await run("depixelate", { block: 0 }, "还原真实分辨率");
    if (!j) return;
    const info = j.info || {};
    log(`  原尺寸 ${info.src_w ?? "?"}×${info.src_h ?? "?"} → ${j.w}×${j.h}，块 ${info.block ?? "?"}，method=${info.method ?? "?"}`, "ok");
    adopt(j, "已还原分辨率");
  }

  // 限色
  async function doQuantize() {
    const n = Number($("rngColors").value);
    const bg = hexToRgb($("bgColor").value);
    const j = await run("quantize", { colors: n, bg }, `限色 ${n}`);
    if (!j) return;
    adopt(j, `限色 ${n} 色`);
    const pal = (j.info.palette || []);
    log("  调色板：" + pal.map((c) => c.hex || c).join(" "), "ok");
    showPalette(pal);
    showResultFiles([{ key: j.key, w: j.w, h: j.h, name: "quantized" }], `限色 ${n}`);
  }

  function showPalette(pal) {
    const box = $("swatches");
    if (!box) return;
    box.innerHTML = "";
    pal.forEach((c) => {
      const hex = (c && c.hex) || (typeof c === "string" ? c : "#000");
      const d = document.createElement("div");
      d.className = "swatch";
      d.style.background = hex;
      d.title = hex;
      d.addEventListener("click", () => {
        navigator.clipboard && navigator.clipboard.writeText(hex);
        log("复制颜色 " + hex);
      });
      box.appendChild(d);
    });
  }

  // 抠底
  async function doRemoveBg() {
    const bg = S.bgTouched ? hexToRgb($("bgColor").value) : null;
    const j = await run("removebg", { bg, tol: 12 }, "抠底");
    if (!j) return;
    adopt(j, "已抠底");
    const c = j.info.bg_corner || [0, 0, 0];
    log(`  背景色 ${c.join(",")}，透明像素占比 ${(j.info.transparent_ratio * 100).toFixed(1)}%`, "ok");
    if (!S.bgTouched) setPicker(c);
  }

  function setPicker(rgb) {
    $("bgColor").value = "#" + rgb.map((v) => (v | 0).toString(16).padStart(2, "0")).join("");
  }

  // 切分
  async function doSlice() {
    const rows = Number($("inRows").value) || 1;
    const cols = Number($("inCols").value) || 1;
    const j = await run("slice", { rows, cols }, `切分 ${cols}×${rows}`);
    if (!j) return;
    S.tiles = j.info.tiles || [];
    S.key = j.key; S.w = j.w; S.h = j.h;
    renderCanvas(); setMeta();
    showResultFiles(S.tiles, `切分 ${cols}列 × ${rows}行`);
    $("bannerText").textContent = `切分 ${cols}列 × ${rows}行 → ${S.tiles.length} 张`;
  }

  // 拼表
  async function doPack() {
    if (!S.tiles.length) { log("先切分，或用结果区里的图元", "err"); return; }
    const cols = Number($("inCols").value) || 4;
    const j = await run("pack", { keys: S.tiles.map((t) => t.key), cols }, `拼表 ${cols} 列`);
    if (!j) return;
    adopt(j, `拼表 ${cols} 列`);
    showResultFiles([{ key: j.key, w: j.w, h: j.h, name: "packed" }], "拼表结果");
    log(`  网格 ${j.info.cols}×${j.info.rows}，单元 ${j.info.cell}px`, "ok");
  }

  // 留白
  async function doPad() {
    const j = await run("pad", { px: 4, square: true }, "加留白");
    if (!j) return;
    adopt(j, "已加留白 4px");
  }

  // 放大
  async function doScale() {
    const j = await run("scale", { scale: 2 }, "放大 ×2");
    if (!j) return;
    adopt(j, "已放大 ×2");
  }

  // ---------------------------------------------------------------- 工具
  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }

  function download(key, name) {
    const a = document.createElement("a");
    a.href = srcOf(key);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    log("已导出 " + name, "ok");
  }

  // ---------------------------------------------------------------- 绑定
  function bind() {
    const drop = $("dropzone");
    const input = $("fileInput");

    const pick = () => input.click();
    drop.addEventListener("click", pick);
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
    $("btnOpenTop").addEventListener("click", pick);
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) openFile(input.files[0]);
      input.value = "";
    });

    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault(); drop.classList.add("is-over");
      }));
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault(); drop.classList.remove("is-over");
      }));
    drop.addEventListener("drop", (e) => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) openFile(f);
    });

    window.addEventListener("paste", (e) => {
      const items = (e.clipboardData && e.clipboardData.items) || [];
      for (const it of items) {
        if (it.type && it.type.indexOf("image") === 0) {
          openFile(it.getAsFile());
          break;
        }
      }
    });

    // 参数
    $("rngColors").addEventListener("input", (e) => {
      $("vColors").textContent = e.target.value;
    });
    ["inRows", "inCols"].forEach((id) =>
      $(id).addEventListener("input", () => {
        $("vSlice").textContent =
          (Number($("inCols").value) || 1) + " × " + (Number($("inRows").value) || 1);
        updateGridOverlay();
      }));
    $("chkGrid").addEventListener("change", updateGridOverlay);
    $("bgColor").addEventListener("input", () => { S.bgTouched = true; });
    $("btnPickBg").addEventListener("click", async () => {
      if (!S.key) { log("先上传图片", "err"); return; }
      const j = await run("probe", {}, "探测四角背景");
      if (!j) return;
      const c = j.info.bg_corner || [0, 0, 0];
      setPicker(c);
      S.bgTouched = true;
      log(`四角背景色 = ${$("bgColor").value}  共 ${j.info.color_count} 色，` +
          `透明占比 ${(j.info.transparent_ratio * 100).toFixed(1)}%`);
    });

    // 操作
    $("opDetect").addEventListener("click", doDetect);
    $("opDepixel").addEventListener("click", doDepixel);
    $("opQuantize").addEventListener("click", doQuantize);
    $("opRemoveBg").addEventListener("click", doRemoveBg);
    $("opSlice").addEventListener("click", doSlice);
    $("opPack").addEventListener("click", doPack);
    $("opPad").addEventListener("click", doPad);
    $("opScale").addEventListener("click", doScale);

    $("btnExport").addEventListener("click", () => {
      if (!S.key) { log("没有可导出的结果", "err"); return; }
      download(S.key, "pxkit-" + S.w + "x" + S.h + ".png");
    });
    $("btnSavePng").addEventListener("click", () => {
      if (!S.key) { log("没有可保存的图像", "err"); return; }
      download(S.key, "pxkit-" + S.w + "x" + S.h + ".png");
    });
    $("btnReset").addEventListener("click", () => {
      if (!S.origKey) { log("还没有导入图片", "err"); return; }
      S.key = S.origKey; S.tiles = [];
      renderCanvas(); setMeta(); clearResult();
      log("已重置到原图");
    });

    // 像素尺寸快捷
    document.querySelectorAll("[data-size]").forEach((b) =>
      b.addEventListener("click", async () => {
        const target = Number(b.dataset.size);
        if (!S.key) { log("先上传图片", "err"); return; }
        const cur = Math.max(S.w, S.h);
        if (cur === target) { log(`已经是 ${target}px`); return; }
        const k = target / cur;
        if (!Number.isFinite(k) || k <= 0) return;
        log(`目标尺寸 ${target}px —— 需要缩放到 ×${k.toFixed(3)}`);
        if (k < 1) {
          $("bannerHint").textContent = `建议：先「还原真实分辨率」再放大到 ${target}`;
        }
      }));

    // 视图
    $("btnFit").addEventListener("click", () => {
      S.zoom = fitZoom(); applyZoom();
    });
    $("btn11").addEventListener("click", () => { S.zoom = 1; applyZoom(); });
    $("btnZoomIn").addEventListener("click", () => {
      S.zoom = Math.min(32, S.zoom * 1.5); applyZoom();
    });
    $("btnZoomOut").addEventListener("click", () => {
      S.zoom = Math.max(0.05, S.zoom / 1.5); applyZoom();
    });
    $("btnToggleGrid").addEventListener("click", () => {
      $("chkGrid").checked = !$("chkGrid").checked;
      updateGridOverlay();
    });
    $("btnToggleChecker").addEventListener("click", (e) => {
      const shell = $("canvasShell");
      shell.classList.toggle("is-light");
      e.currentTarget.classList.toggle("is-gold");
    });

    // 日志折叠
    $("logHead").addEventListener("click", () => {
      const bar = $("logbar");
      bar.classList.toggle("open");
      $("logToggle").textContent = bar.classList.contains("open") ? "收起 ▾" : "展开 ▴";
    });

    // 导航（仅视觉）
    document.querySelectorAll("[data-nav]").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-nav]").forEach((x) => x.classList.remove("is-active"));
        b.classList.add("is-active");
        log("切换到「" + b.textContent + "」— 当前版本只实现创作页");
      }));

    window.addEventListener("resize", () => {
      if (S.key) { S.zoom = fitZoom(); applyZoom(); }
    });
  }

  // 把调色板容器补进 DOM（避免改 HTML 结构过多）
  document.addEventListener("DOMContentLoaded", () => {
    const anchor = $("vColors").closest(".field");
    const box = document.createElement("div");
    box.className = "swatches";
    box.id = "swatches";
    anchor.appendChild(box);
    bind();
    log("pxkit 前端已就绪 — 拖入图片或点击「打开图片」");
  });
})();
