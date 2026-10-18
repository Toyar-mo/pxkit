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

    // 导航（仅视觉；设置项由 bindSettings 打开弹层）
    document.querySelectorAll("[data-nav]").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-nav]").forEach((x) => x.classList.remove("is-active"));
        b.classList.add("is-active");
        if (b.dataset.nav === "settings") return;
        if (b.dataset.nav === "gen") {
          showPage("gen");
          log("切换到「生成」页 — 直接调用 rika 原后端");
          return;
        }
        showPage("create");
        log("切换到「" + b.textContent + "」页");
      }));

    window.addEventListener("resize", () => {
      if (S.key) { S.zoom = fitZoom(); applyZoom(); }
    });
  }

  // ---------------------------------------------------------------- 设置页
  // 自定义模型配置：直接指向 rika 原后端。后端（rika.py）只做转发。
  const apiGet = async (path) => {
    const r = await fetch(path, { method: "GET" });
    const j = await r.json().catch(() => ({ error: "响应不是 JSON" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  };

  function cfgStatus(text, kind) {
    const el = $("cfgStatus");
    el.textContent = text;
    el.className = "cfg-status" + (kind ? " " + kind : "");
  }

  function cfgFill(c) {
    $("cfgBase").value = c.base_url || "";
    $("cfgBase").placeholder = c.default_base_url || "";
    $("cfgModel").value = c.model || "";
    $("cfgTimeout").value = c.timeout;
    $("cfgInterval").value = c.poll_interval;
    $("cfgPoll").value = c.poll_timeout;
    $("cfgKey").value = "";
    $("cfgKey").placeholder = c.has_key ? c.api_key_masked : "粘贴 X-API-Key";
    $("cfgKeyHint").textContent = c.has_key
      ? "已保存 " + c.api_key_masked + " —— 留空保持不变。"
      : "尚未配置，填入后点「测试连接」验证。";
    const box = $("cfgEndpoints");
    box.innerHTML = "";
    (c.endpoints || []).forEach((e) => {
      const row = document.createElement("div");
      row.className = "cfg-ep";
      row.innerHTML = '<span class="k"></span><span class="p"></span><span class="c"></span>';
      row.querySelector(".k").textContent = e.kind;
      row.querySelector(".p").textContent = e.path;
      row.querySelector(".c").textContent = e.credits + " 积分";
      box.appendChild(row);
    });
  }

  function cfgPayload() {
    const p = {
      base_url: $("cfgBase").value.trim(),
      model: $("cfgModel").value.trim(),
      timeout: Number($("cfgTimeout").value) || undefined,
      poll_interval: Number($("cfgInterval").value) || undefined,
      poll_timeout: Number($("cfgPoll").value) || undefined,
    };
    const k = $("cfgKey").value.trim();
    if (k) p.api_key = k;   // 留空 = 不改动已保存的 key
    return p;
  }

  async function cfgLoad() {
    try {
      cfgFill((await apiGet("/api/config")).config);
    } catch (e) {
      cfgStatus("读取配置失败：" + e.message, "err");
    }
  }

  function cfgOpen() {
    $("cfgModal").hidden = false;
    cfgStatus("就绪");
    cfgLoad();
    setTimeout(() => $("cfgBase").focus(), 30);
  }

  function cfgClose() { $("cfgModal").hidden = true; }

  async function cfgTest() {
    cfgStatus("测试中…");
    try {
      const j = await api("/api/config/test", cfgPayload());
      cfgStatus(`连接成功 · 余额 ${j.credits} 积分 · ${j.base_url}`, "ok");
      log(`后端连通，余额 ${j.credits} 积分`);
    } catch (e) {
      cfgStatus("测试失败：" + e.message, "err");
      log("后端测试失败：" + e.message);
    }
  }

  async function cfgSave() {
    cfgStatus("保存中…");
    try {
      const j = await api("/api/config", cfgPayload());
      cfgFill(j.config);
      cfgStatus("已保存。", "ok");
      log("模型配置已保存 → " + j.config.base_url +
          (j.config.model ? " · model=" + j.config.model : ""));
    } catch (e) {
      cfgStatus("保存失败：" + e.message, "err");
    }
  }

  function bindSettings() {
    $("btnSettings").addEventListener("click", cfgOpen);
    const navBtn = document.querySelector('[data-nav="settings"]');
    if (navBtn) navBtn.addEventListener("click", cfgOpen);
    $("cfgClose").addEventListener("click", cfgClose);
    $("cfgModal").addEventListener("click", (e) => {
      if (e.target === $("cfgModal")) cfgClose();
    });
    $("cfgTest").addEventListener("click", cfgTest);
    $("cfgSave").addEventListener("click", cfgSave);
    $("cfgKey").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); cfgTest(); }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("cfgModal").hidden) cfgClose();
    });
  }

/* pxkit web —— 生成页逻辑：(function(){...})() 内追加 */
  // ================================================================ 生成页
  // 参数只是搬运工：本地按 rika 前端同样的规则预处理参考图，
  // 再把官方字段原样转发给 rika 后端。这里不做任何自己的推断。
  const GEN = {
    kind: "v2",
    refs: [null, null, null],   // {img, b64, preview, outW, outH}
    activeSlot: 0,
    busy: false,
    timer: null,
    playTimer: null,
    frames: 0,
    credits: null,
    job: null,
  };

  const GEN_FRAMES = { v1: [8, 10, 12, 14, 16], v2: [16, 20, 24, 32] };
  const GEN_COST = { v1: { now: 4, was: 5 }, v2: { now: 6, was: 8 } };
  const GEN_KIND_HINT = {
    v1: "V1 · 4 积分／次 · 预设动作，输出 8–16 帧",
    v2: "V2 · 6 积分／次 · 自由提示词，输出 16–32 帧",
  };
  const GEN_SLOT_NAME = ["起始", "中间", "结束"];

  // V1 预设动作的默认提示词（取自 rika 前端 ir 表，逐字一致）
  const GEN_MOTION_PROMPT = {
    idle: "side-view, 2D game character idle animation, body slightly sways.",
    walk: "side-view, 2D game character walks forward.",
    run: "side-view, 2D game character runs forward.",
    jump: "side-view, 2D game character jumps in place,takeoff then fall",
    attack: "side-view, 2D game character raises weapon to perform a powerful strike forward.",
    hit: "side-view, 2D game character getting hit and knocked backward.",
    defeated: "side-view 2D game character getting hit, kneel down and fall to the ground, lying motionlessly",
  };
  const GEN_ATTACK_RANGED =
    "side-view, 2D game character points his weapon forward and shoot a powerful bullet.";
  const GEN_ATTACK_OTHER = "[describe the attack]";

  /** 切动作时的帧数（rika: attack/defeated → 16，其余 → 12），吸附到 V1 预设。 */
  function genMotionFrames(motion) {
    const want = (motion === "attack" || motion === "defeated") ? 16 : 12;
    const list = GEN_FRAMES.v1;
    return list.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a), list[0]);
  }

  /** 按动作/攻击子类型给出提示词 + 噪声（rika 的 useEffect 原样搬运）。 */
  function genMotionPreset(motion, attack) {
    if (motion === "attack") {
      if (attack === "ranged") return { prompt: GEN_ATTACK_RANGED, noise: 0.3 };
      if (attack === "other") return { prompt: GEN_ATTACK_OTHER, noise: 0.3 };
      return { prompt: GEN_MOTION_PROMPT.attack, noise: 0.6 };
    }
    return { prompt: GEN_MOTION_PROMPT[motion] || "", noise: 0.6 };
  }

  // ---------------------------------------------------------------- 形状工具
  const padStep = (v) => Math.max(0, Math.round((Number.isFinite(+v) ? +v : 0) / 32) * 32);

  /** rika 的 Ps：把四边留白夹到"每边 32 的倍数、总和不超过 256-尺寸"。 */
  function genClampPad(size, pad) {
    const l = Math.min(256 - size.x, padStep(pad.left));
    const r = Math.min(256 - size.x - l, padStep(pad.right));
    const t = Math.min(256 - size.y, padStep(pad.top));
    const b = Math.min(256 - size.y - t, padStep(pad.bottom));
    return { left: l, right: r, top: t, bottom: b };
  }

  /** rika 的 mh：把留白加进像素尺寸。 */
  function genPadSize(size, pad) {
    return { x: size.x + pad.left + pad.right, y: size.y + pad.top + pad.bottom };
  }

  function genSizeVec() {
    if (GEN.kind === "v1") {
      const s = parseInt($("genSize").value, 10);
      return { x: s, y: s };
    }
    return { x: +$("genSizeX").value, y: +$("genSizeY").value };
  }

  function genPadVec() {
    if (!$("genPad").checked) return { left: 0, right: 0, top: 0, bottom: 0 };
    return genClampPad(genSizeVec(), {
      left: $("genPadLeft").value,
      right: $("genPadRight").value,
      top: $("genPadTop").value,
      bottom: $("genPadBottom").value,
    });
  }

  /** V1 的 scale_factor —— 与 rika 前端逐字一致。 */
  function genScaleFactor(size) {
    if ($("genPad").checked) {
      if (size === 256) return 2;
      if (size === 32) return 12;
      return 384 / size;
    }
    if (size === 32 || size === 64) return 512 / size;
    return 768 / size;
  }

  // ---------------------------------------------------------------- 模型切换
  function genSlider() {
    const box = $("genFrames");
    const list = GEN_FRAMES[GEN.kind] || GEN_FRAMES.v2;
    const prev = +box.value;
    box.textContent = "";
    list.forEach((n) => {
      const o = document.createElement("option");
      o.value = String(n);
      o.textContent = n + " 帧";
      box.appendChild(o);
    });
    box.value = list.indexOf(prev) >= 0 ? String(prev) : String(list[Math.min(2, list.length - 1)]);
  }

  function genSetKind(kind) {
    GEN.kind = kind === "v1" ? "v1" : "v2";
    const v1 = GEN.kind === "v1";
    document.querySelectorAll("#genSeg .seg-item").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.kind === GEN.kind));
    $("genKindHint").textContent = GEN_KIND_HINT[GEN.kind];

    const c = GEN_COST[GEN.kind];
    $("genCost").textContent = String(c.now);
    $("genCostOld").textContent = String(c.was);

    // V1 用字符串 pixel_size + 预设动作；V2 用 {x,y} + 自由提示词
    $("genSize").hidden = !v1;
    $("genSizeXY").hidden = v1;
    $("genMotionWrap").hidden = !v1;
    $("genAttackWrap").hidden = !v1 || $("genMotion").value !== "attack";
    $("genNoiseWrap").hidden = !v1;
    $("genExpandWrap").hidden = v1;             // ai_expand 只属于 V2
    // pixel_size 改成 8×8 那种写法时 V1 就不该显示噪声之外的差异了；
    // 留白：V1 用 9 宫格位置，V2 用四边数值 —— 开关共用
    $("genPadWrap").hidden = false;
    genSyncPadUI();
    genSyncSizeLabel();
    genSlider();
    genCostGuard();
    genRasterizeAll();
  }

  function genSyncPadUI() {
    const v1 = GEN.kind === "v1";
    const on = $("genPad").checked;
    $("genPadPosWrap").hidden = !(v1 && on);
    $("genPadXYWrap").hidden = !(!v1 && on);
    if (!v1 && on) {
      const sz = genSizeVec();
      const pad = genPadVec();
      const out = genPadSize(sz, pad);
      $("genPadHint").textContent =
        "提交 pixel_size = " + sz.x + "×" + sz.y + " + 留白 → " + out.x + "×" + out.y;
    }
  }

  function genSyncSizeLabel() {
    const sz = genSizeVec();
    $("genSizeLabel").textContent = sz.x + " × " + sz.y;
  }

  // ---------------------------------------------------------------- 参数拼装
  function genPromptValue() {
    let prompt = $("genPrompt").value.trim();
    if (!prompt && GEN.kind === "v1") {
      const m = $("genMotion").value;
      prompt = (m && GEN_MOTION_PROMPT[m]) || GEN_MOTION_PROMPT.idle;
    }
    return prompt;
  }

  function genSeedValue() {
    if ($("genFixSeed").checked) return Math.max(0, Math.floor(+$("genSeed").value || 0));
    return Math.floor(Math.random() * 1e6);   // 与 rika 一致：不固定就随机
  }

  /** 严格对齐官方字段名。返回 {params, images? }。 */
  function genParams() {
    const frames = +$("genFrames").value;
    GEN.frames = frames;

    const prompt = genPromptValue();
    const hasMid = !!GEN.refs[1];
    const hasEnd = !!GEN.refs[2];
    const seed = genSeedValue();

    if (GEN.kind === "v1") {
      const size = parseInt($("genSize").value, 10);
      const motion = $("genMotion").value || "idle";
      const pad = $("genPad").checked;
      // rika V1 是 {...state} 全展开，多带的键后端会忽略；此处保留同样语义
      const p = {
        prompt: prompt,
        motion_type: motion,
        attack_type: motion === "attack" ? $("genAttack").value : "melee",
        pixel_size: String(size),
        length: 2 * frames + 1,
        use_padding: pad,
        padding_position: pad ? $("genPadPos").value : "mc",
        scale_factor: genScaleFactor(size),
        noise_strength: +$("genNoise").value,
        seed: seed,
        fix_seed: $("genFixSeed").checked,
        use_mid_image: hasMid,
        use_end_image: hasEnd,
        use_quantization: $("genQuant").checked,
        quantization_colors: +$("genColors").value,
        bg_color: $("genBg").value,
      };
      return p;
    }

    // V2：只有这 8 个键
    const sz = genSizeVec();
    const pad = genPadVec();
    return {
      prompt: prompt,
      pixel_size: genPadSize(sz, pad),
      padding: pad,
      ai_expand: $("genExpand").checked,
      seed: seed,
      output_frames: frames,
      use_mid_image: hasMid,
      use_end_image: hasEnd,
    };
  }

  // ---------------------------------------------------------------- 参考图
  function genSlotEl(i) { return document.querySelector('.refslot[data-slot="' + i + '"]'); }
  function genSlotPaint(i) {
    const el = genSlotEl(i);
    if (!el) return;
    const img = el.querySelector(".refthumb");
    const r = GEN.refs[i];
    if (r) {
      if (r.preview && img.getAttribute("src") !== r.preview) img.src = r.preview;
      img.hidden = false;
      el.classList.add("has-img");
      if (!el.querySelector(".refdel")) {
        const btn = document.createElement("button");
        btn.className = "refdel";
        btn.textContent = "✕";
        btn.title = "移除";
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          GEN.refs[i] = null;
          genSlotPaint(i);
          log("参考图「" + GEN_SLOT_NAME[i] + "」已移除");
        });
        el.appendChild(btn);
      }
    } else {
      img.hidden = true;
      img.removeAttribute("src");
      el.classList.remove("has-img");
      const d = el.querySelector(".refdel");
      if (d) d.remove();
    }
    el.classList.toggle("is-active", GEN.activeSlot === i);
  }

  function genSelectSlot(i) {
    GEN.activeSlot = i;
    [0, 1, 2].forEach(genSlotPaint);
    $("genRefHint").textContent = "当前选中：「" + GEN_SLOT_NAME[i] + "」" +
      (GEN.refs[i] ? "（可旋转 / 移除）" : "（空，点一下选图）");
  }

  function genPickImage(slot) {
    GEN.pendingSlot = slot;
    const input = $("genFile");
    input.value = "";
    input.click();
  }

  function genIngest(file, slot) {
    const rd = new FileReader();
    rd.onload = () => {
      const im = new Image();
      im.onload = () => {
        GEN.refs[slot] = { img: im };
        genRasterize(slot);
        // V1 放了中间/结束图时后端只认 16 帧（rika 的 useEffect 同款约束）
        if (GEN.kind === "v1" && slot > 0) {
          if ($("genFrames").value !== "16") {
            $("genFrames").value = "16";
            log("V1 放了「" + GEN_SLOT_NAME[slot] + "」图 —— 帧数已锁定为 16");
          }
        }
        log("参考图「" + GEN_SLOT_NAME[slot] + "」已载入：" + file.name +
            "（" + Math.round(file.size / 1024) + " KB）");
      };
      im.onerror = () => log("图片无法解码：" + file.name, "err");
      im.src = String(rd.result || "");
    };
    rd.onerror = () => log("文件读取失败：" + file.name, "err");
    rd.readAsDataURL(file);
  }

  /** 中位切分量化（只在勾了「输入图调色盘」时用）。 */
  function genQuantize(cx, w, h, colors) {
    const img = cx.getImageData(0, 0, w, h);
    const d = img.data;
    const px = [];
    for (let i = 0; i < d.length; i += 4) px.push([d[i], d[i + 1], d[i + 2], i]);
    if (px.length <= colors) return;

    const cut = (list, depth) => {
      if (depth === 0 || list.length <= 1) return [list];
      let rMin = 255, rMax = 0, gMin = 255, gMax = 0, bMin = 255, bMax = 0;
      list.forEach((q) => {
        rMin = Math.min(rMin, q[0]); rMax = Math.max(rMax, q[0]);
        gMin = Math.min(gMin, q[1]); gMax = Math.max(gMax, q[1]);
        bMin = Math.min(bMin, q[2]); bMax = Math.max(bMax, q[2]);
      });
      const ch = (rMax - rMin >= gMax - gMin && rMax - rMin >= bMax - bMin) ? 0
               : (gMax - gMin >= bMax - bMin ? 1 : 2);
      list.sort((x, y) => x[ch] - y[ch]);
      const mid = Math.floor(list.length / 2);
      return cut(list.slice(0, mid), depth - 1).concat(cut(list.slice(mid), depth - 1));
    };

    const depth = Math.max(1, Math.min(8, Math.floor(Math.log2(colors))));
    const boxes = cut(px, depth).filter((bx) => bx.length);
    boxes.forEach((bx) => {
      let r = 0, g = 0, b = 0;
      bx.forEach((q) => { r += q[0]; g += q[1]; b += q[2]; });
      const n = bx.length;
      const rr = Math.round(r / n), gg = Math.round(g / n), bb = Math.round(b / n);
      bx.forEach((q) => { d[q[3]] = rr; d[q[3] + 1] = gg; d[q[3] + 2] = bb; });
    });
    cx.putImageData(img, 0, 0);
  }

  /** 参考图 → 目标像素尺寸画布（填背景色 + 可选量化）→ base64。
   *  V2 还要再套一层 padding 画布，因为提交的 pixel_size 是含留白的尺寸。 */
  function genRasterize(slot) {
    const r = GEN.refs[slot];
    if (!r || !r.img) return;
    const sz = genSizeVec();

    const cv = document.createElement("canvas");
    cv.width = sz.x;
    cv.height = sz.y;
    const cx = cv.getContext("2d", { willReadFrequently: true });
    cx.imageSmoothingEnabled = false;
    const src = r.img;
    const sw = src.naturalWidth, sh = src.naturalHeight;
    const fit = Math.min(sz.x / sw, sz.y / sh);
    const dw = Math.max(1, Math.round(sw * fit));
    const dh = Math.max(1, Math.round(sh * fit));
    cx.drawImage(src, Math.floor((sz.x - dw) / 2), Math.floor((sz.y - dh) / 2), dw, dh);

    // 透明处填背景色（rika 的 destination-over 效果）
    cx.globalCompositeOperation = "destination-over";
    cx.fillStyle = $("genBg").value || "#004040";
    cx.fillRect(0, 0, sz.x, sz.y);
    cx.globalCompositeOperation = "source-over";

    if ($("genQuant").checked) {
      genQuantize(cx, sz.x, sz.y, Math.max(2, +$("genColors").value));
    }

    let outCv = cv;
    const pad = genPadVec();
    if (GEN.kind === "v2" && (pad.left || pad.right || pad.top || pad.bottom)) {
      const out = genPadSize(sz, pad);
      outCv = document.createElement("canvas");
      outCv.width = out.x;
      outCv.height = out.y;
      const oc = outCv.getContext("2d");
      oc.imageSmoothingEnabled = false;
      oc.fillStyle = $("genBg").value || "#004040";
      oc.fillRect(0, 0, out.x, out.y);
      oc.drawImage(cv, pad.left, pad.top);
    }

    const url = outCv.toDataURL("image/png");
    r.preview = url;
    r.b64 = url.slice(url.indexOf(",") + 1);
    r.outW = outCv.width;
    r.outH = outCv.height;
    genSlotPaint(slot);
  }

  function genRasterizeAll() { [0, 1, 2].forEach((i) => { if (GEN.refs[i]) genRasterize(i); }); }

  function genRotate(flip) {
    const i = GEN.activeSlot;
    const r = GEN.refs[i];
    if (!r || !r.img) {
      log("当前选中的「" + GEN_SLOT_NAME[i] + "」还是空的 —— 先选图再旋转 / 翻转", "err");
      return;
    }
    const src = r.img;
    const cv = document.createElement("canvas");
    if (flip) {
      cv.width = src.naturalWidth;
      cv.height = src.naturalHeight;
      const cx = cv.getContext("2d");
      cx.translate(cv.width, 0);
      cx.scale(-1, 1);
      cx.drawImage(src, 0, 0);
    } else {
      cv.width = src.naturalHeight;
      cv.height = src.naturalWidth;
      cv.getContext("2d").drawImage(src, 0, 0, cv.width, cv.height);
    }
    const im = new Image();
    im.onload = () => {
      r.img = im;
      genRasterize(i);
      log("参考图「" + GEN_SLOT_NAME[i] + "」已" + (flip ? "水平翻转" : "旋转 90°"));
    };
    im.src = cv.toDataURL("image/png");
  }

  // ---------------------------------------------------------------- 余额
  async function genLoadCredits() {
    const el = $("genCredits");
    try {
      const j = await api("/api/credits", {});
      const c = (j && (j.credits !== undefined ? j.credits : j.balance));
      GEN.credits = (c === undefined || c === null) ? null : c;
      el.textContent = GEN.credits === null ? "—" : String(GEN.credits);
      el.title = "";
    } catch (e) {
      GEN.credits = null;
      el.textContent = "?";
      el.title = e.message;
    }
    genCostGuard();
  }

  function genCostGuard() {
    const btn = $("genRun");
    if (!btn || GEN.busy) return;
    const need = GEN_COST[GEN.kind].now;
    const have = GEN.credits;
    const short = have !== null && have !== undefined && have < need;
    btn.classList.toggle("is-short", short);
    btn.title = short ? "余额 " + have + " 积分 < 本次需要 " + need + " 积分" : "";
  }

  // ---------------------------------------------------------------- 提交
  function genBusy(on, label) {
    GEN.busy = on;
    $("genRun").disabled = on;
    $("genRunLabel").textContent = on ? (label || "生成中…") : "开始生成";
  }

  async function genRun() {
    if (GEN.busy) return;

    const images = [];
    GEN.refs.forEach((r) => { if (r && r.b64) images.push(r.b64); });
    if (!images.length) {
      log("先上传「起始」参考图 —— /generate* 至少要一张输入图", "err");
      genSelectSlot(0);
      return;
    }
    if (GEN.refs[1] && !$("genPrompt").value.trim() && GEN.kind === "v2") {
      log("已放中间图，但提示词为空 —— V2 要求提示词必填", "err");
      $("genPrompt").focus();
      return;
    }
    const params = genParams();
    if (!String(params.prompt || "").trim()) {
      log("提示词不能为空", "err");
      $("genPrompt").focus();
      return;
    }
    const need = GEN_COST[GEN.kind].now;
    if (GEN.credits !== null && GEN.credits < need) {
      log("积分不足：" + GEN.kind.toUpperCase() + " 需要 " + need +
          "，当前余额 " + GEN.credits, "err");
      return;
    }

    genBusy(true, "提交中…");
    const brief = JSON.stringify(params);
    log("提交 " + GEN.kind.toUpperCase() + " · " + images.length + " 图 → " +
        (brief.length > 240 ? brief.slice(0, 240) + "…" : brief));

    try {
      const j = await api("/api/gen", { kind: GEN.kind, images: images, params: params });
      log("已受理 gen_id=" + String(j.gen_id).slice(0, 8) + "（" + j.credits + " 积分）", "ok");
      genBusy(true, "排队中…");
      genPoll(j.gen_id);
    } catch (e) {
      log("生成失败：" + e.message, "err");
      genBusy(false);
      genLoadCredits();
    }
  }

  function genPoll(genId) {
    const t0 = Date.now();
    const LIMIT = 15 * 60 * 1000;
    let last = "";
    const tick = async () => {
      if (!GEN.busy) return;
      try {
        const j = await api("/api/job", { gen_id: genId });
        const job = j.job || {};
        const st = String(job.status || "unknown");
        const stage = job.processing_stage ? " · " + job.processing_stage : "";
        if (st + stage !== last) {
          last = st + stage;
          log("状态：" + st + stage + "（已等 " + Math.round((Date.now() - t0) / 1000) + "s）");
          if (st === "queued") genBusy(true, "排队中…");
          else if (st === "running") genBusy(true, "生成中…");
        }
        if (st === "succeeded") {
          GEN.job = job;
          log("生成完成", "ok");
          genBusy(false);
          genRender(job);
          genLoadCredits();
          return;
        }
        if (st === "failed" || st === "error") {
          log("任务失败：" + (job.error || "后端未给原因"), "err");
          genBusy(false);
          genLoadCredits();
          return;
        }
        if (Date.now() - t0 > LIMIT) {
          log("轮询超时（15 分钟）。gen_id=" + genId + " —— 任务可能仍在跑，稍后手动查", "err");
          genBusy(false);
          return;
        }
        GEN.timer = setTimeout(tick, 3000);
      } catch (e) {
        log("轮询出错：" + e.message, "err");
        genBusy(false);
      }
    };
    tick();
  }

  // ---------------------------------------------------------------- 结果
  function genRender(job) {
    const box = $("genResult");
    const outs = (job && job.output_images) || [];
    const url = outs[0] && (outs[0].url || outs[0]);
    box.textContent = "";
    if (!url || typeof url !== "string") {
      const d = document.createElement("div");
      d.className = "gen-empty";
      d.textContent = "任务成功，但没拿到输出图 —— API Key 模式下结果图不长期保留。";
      box.appendChild(d);
      return;
    }

    const wrap = document.createElement("div");
    wrap.className = "gen-sheet-wrap";
    const im = document.createElement("img");
    im.className = "gen-sheet";
    im.alt = "生成结果";
    im.src = url;
    wrap.appendChild(im);
    box.appendChild(wrap);

    const meta = document.createElement("div");
    meta.className = "gen-meta";
    [["gen", String(job.gen_id || "—").slice(0, 8)],
     ["type", job.job_type || GEN.kind],
     ["status", job.status || "—"]].forEach(([k, v]) => {
      const sp = document.createElement("span");
      sp.innerHTML = k + " <b></b>";
      sp.querySelector("b").textContent = v;
      meta.appendChild(sp);
    });
    box.appendChild(meta);

    const acts = document.createElement("div");
    acts.className = "gen-actions";
    const mk = (label, cls, fn) => {
      const btn = document.createElement("button");
      btn.className = "pixel-btn is-sm" + (cls ? " " + cls : "");
      btn.textContent = label;
      btn.addEventListener("click", fn);
      acts.appendChild(btn);
    };
    mk("打开原图", "", () => window.open(url, "_blank", "noopener"));
    mk("同参数重跑", "is-ghost", genRun);
    mk("送去创作页", "is-ghost", () => genSendToCreate(url));
    box.appendChild(acts);

    im.addEventListener("load", () => genSlice(box, im, url));
  }

  /** 按帧数把整条精灵表切开做逐帧预览 + 播放。 */
  function genSlice(box, im, url) {
    const f = GEN.frames;
    const w = im.naturalWidth, h = im.naturalHeight;
    if (!f || !w || w % f !== 0) {
      const t = document.createElement("div");
      t.className = "gen-strip-title";
      t.textContent = "精灵表 " + w + "×" + h + " —— 宽度不能被 " +
                      (f || "?") + " 整除，没自动切帧";
      box.appendChild(t);
      return;
    }
    const fw = w / f;

    const t1 = document.createElement("div");
    t1.className = "gen-strip-title";
    t1.textContent = "逐帧（" + f + " 帧 · 每帧 " + fw + "×" + h + "）";
    box.appendChild(t1);

    const cell = 56;
    const scale = cell / fw;
    const strip = document.createElement("div");
    strip.className = "gen-strip";
    for (let i = 0; i < f; i += 1) {
      const d = document.createElement("div");
      d.className = "fr";
      d.title = "第 " + (i + 1) + " 帧";
      d.style.backgroundImage = "url(" + url + ")";
      d.style.backgroundSize = (w * scale) + "px auto";
      d.style.backgroundPosition = (-i * fw * scale) + "px 0";
      strip.appendChild(d);
    }
    box.appendChild(strip);

    const t2 = document.createElement("div");
    t2.className = "gen-strip-title";
    t2.textContent = "播放";
    box.appendChild(t2);

    const pv = document.createElement("div");
    pv.className = "gen-preview";
    box.appendChild(pv);

    const playScale = Math.max(1, Math.floor(160 / fw));
    pv.style.width = (fw * playScale) + "px";
    pv.style.height = (h * playScale) + "px";
    pv.style.backgroundImage = "url(" + url + ")";
    pv.style.backgroundSize = (w * playScale) + "px auto";
    pv.style.backgroundRepeat = "no-repeat";

    let i = 0;
    const step = () => {
      pv.style.backgroundPosition = (-i * fw * playScale) + "px 0";
      i = (i + 1) % f;
    };
    step();
    if (GEN.playTimer) clearInterval(GEN.playTimer);
    if ($("genLoop").checked) GEN.playTimer = setInterval(step, 1000 / 12);
  }

  async function genSendToCreate(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const blob = await r.blob();
      const file = new File([blob], "gen-" + Date.now() + ".png",
                            { type: blob.type || "image/png" });
      genCancel();
      showPage("create");
      document.querySelectorAll("[data-nav]").forEach((x) =>
        x.classList.toggle("is-active", x.dataset.nav === "create"));
      await openFile(file);
      log("生成结果已送入创作页", "ok");
    } catch (e) {
      log("送去创作页失败：" + e.message + "（多是 CDN 跨域）", "err");
    }
  }

  function genCancel() {
    if (GEN.timer) { clearTimeout(GEN.timer); GEN.timer = null; }
    if (GEN.playTimer) { clearInterval(GEN.playTimer); GEN.playTimer = null; }
  }

  // ---------------------------------------------------------------- 绑定
  function bindGen() {
    if (!$("pageGen")) return;

    $("genAdvToggle").addEventListener("click", () => {
      const open = $("genAdv").hidden;
      $("genAdv").hidden = !open;
      $("genAdvToggle").textContent = open ? "高级选项 ▴" : "高级选项 ▾";
    });

    $("genNoise").addEventListener("input", () => {
      $("genNoiseV").textContent = (+$("genNoise").value).toFixed(1);
    });
    $("genColors").addEventListener("input", () => {
      $("genColorsV").textContent = $("genColors").value;
      genRasterizeAll();
    });
    $("genQuant").addEventListener("change", genRasterizeAll);
    $("genBg").addEventListener("change", genRasterizeAll);
    $("genBg").addEventListener("input", () => {
      if ($("genBgHex")) $("genBgHex").textContent = $("genBg").value.toUpperCase();
      genRasterizeAll();
    });

    document.querySelectorAll("#genSeg .seg-item").forEach((b) =>
      b.addEventListener("click", () => genSetKind(b.dataset.kind)));

    document.querySelectorAll(".refslot").forEach((el) => {
      const slot = +el.dataset.slot;
      el.addEventListener("click", (e) => {
        if (e.target.classList.contains("refdel")) return;
        genSelectSlot(slot);
        genPickImage(slot);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); genPickImage(slot); }
      });
    });

    $("genClearRefs").addEventListener("click", () => {
      GEN.refs = [null, null, null];
      [0, 1, 2].forEach(genSlotPaint);
      genSelectSlot(GEN.activeSlot);
      log("参考图已清空");
    });
    $("genRotate").addEventListener("click", () => genRotate(false));
    if ($("genFlip")) $("genFlip").addEventListener("click", () => genRotate(true));
    $("genCreditsRefresh").addEventListener("click", genLoadCredits);

    // 动作 / 攻击子类型联动：提示词、噪声、帧数（逐字对齐 rika 的 useEffect）
    const genSyncMotion = () => {
      const m = $("genMotion").value;
      $("genAttackWrap").hidden = m !== "attack";
      if (!m) return;

      const cur = $("genPrompt").value.trim();
      const preset = genMotionPreset(m, $("genAttack").value);
      const known = Object.values(GEN_MOTION_PROMPT)
        .concat([GEN_ATTACK_RANGED, GEN_ATTACK_OTHER]);

      // 用户自己改过提示词就不动它（rika 的 ia.current 守卫）
      if (!cur || known.indexOf(cur) >= 0) {
        $("genPrompt").value = preset.prompt;
        GEN.promptTouched = false;
      }
      $("genNoise").value = preset.noise;
      $("genNoiseV").textContent = preset.noise.toFixed(1);

      const f = genMotionFrames(m);
      if ([...$("genFrames").options].some((o) => +o.value === f)) $("genFrames").value = String(f);
    };

    $("genMotion").addEventListener("change", genSyncMotion);
    $("genAttack").addEventListener("change", genSyncMotion);
    $("genPrompt").addEventListener("input", () => { GEN.promptTouched = true; });

    $("genPad").addEventListener("change", genSyncPadUI);
    ["genPadLeft", "genPadRight", "genPadTop", "genPadBottom"].forEach((id) =>
      $(id).addEventListener("change", () => { genSyncPadUI(); genRasterizeAll(); }));
    $("genFixSeed").addEventListener("change", () => {
      $("genSeedWrap").hidden = !$("genFixSeed").checked;
    });

    ["genSize", "genSizeX", "genSizeY"].forEach((id) =>
      $(id).addEventListener("change", () => {
        genSyncSizeLabel();
        genSyncPadUI();
        genRasterizeAll();
      }));

    $("genFile").addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      genIngest(f, GEN.pendingSlot === undefined ? 0 : GEN.pendingSlot);
    });

    $("genRun").addEventListener("click", genRun);
    $("genPrompt").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); genRun(); }
    });

    genSetKind("v2");
    genSelectSlot(0);
    genLoadCredits();

    // 只读测试钩子：只拼参数 / 切片，不提交、不扣积分。
    window.__genTest = {
      params: genParams,
      slice: genSlice,
      run: genRun,
      ingest: genIngest,
      clampPad: genClampPad,
      padSize: genPadSize,
      scaleFactor: genScaleFactor,
      state: GEN,
      set frames(v) { GEN.frames = v; },
      get frames() { return GEN.frames; },
    };
  }

  // 页面切换：生成 / 创作
  function showPage(name) {
    const gen = name === "gen";
    $("pageGen").hidden = !gen;
    $("pageCreate").hidden = gen;
    // 顶部横幅跟着页面走 —— 两个页面的算力位置完全不同，别误导
    const tag = $("bannerTag");
    const txt = $("bannerText");
    const hint = $("bannerHint");
    if (tag && txt && hint) {
      if (gen) {
        tag.textContent = "在线";
        txt.textContent = "生成走 rika 原后端 —— 需要先在「设置」里填 API Key，每次生成按端点扣积分。";
        hint.textContent = "上传起始图 + 写提示词";
      } else {
        tag.textContent = "本地";
        txt.textContent = "像素算法全部在本地运行 —— 客户端只做界面，图像处理走 pxkit。";
        hint.textContent = "拖入图片即可开始";
      }
    }
  }

  // 把调色板容器补进 DOM（避免改 HTML 结构过多）
  document.addEventListener("DOMContentLoaded", () => {
    const anchor = $("vColors").closest(".field");
    const box = document.createElement("div");
    box.className = "swatches";
    box.id = "swatches";
    anchor.appendChild(box);
    bind();
    bindSettings();
    bindGen();
    // 支持 #gen 深链，方便直接打开生成页
    const deep = location.hash === "#gen" ? "gen" : "create";
    showPage(deep);
    document.querySelectorAll("[data-nav]").forEach((x) =>
      x.classList.toggle("is-active", x.dataset.nav === deep));
    log("pxkit 前端已就绪 — 拖入图片或点击「打开图片」");
  });
})();
