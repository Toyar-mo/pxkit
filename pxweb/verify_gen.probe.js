(async () => {
  const out = { errors: [], checks: [] };
  window.addEventListener("error", (e) => out.errors.push(String(e.message)));
  const $ = (id) => document.getElementById(id);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const ok = (name, cond, extra) => out.checks.push({ name, pass: !!cond, extra: extra === undefined ? null : extra });
  const lastLog = () => {
    const all = document.querySelectorAll("#logBody .ln");
    return all.length ? all[all.length - 1].textContent : "";
  };
  const logN = () => document.querySelectorAll("#logBody .ln").length;

  // 前置：这份断言写的是 rika 云端契约（有积分、要参考图）。
  // 当前后端若是本地 ComfyUI，先切回 rika，否则价格/参考图断言必然假失败。
  const K0 = window.__genTest;
  if (K0 && K0.isFree && K0.isFree()) {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend: "rika" }),
    });
    await K0.loadCaps();
    await wait(400);
  }

  // 切到生成页
  const navGen = document.querySelector('[data-nav="gen"]');
  if (navGen) navGen.click();
  await wait(200);
  ok("生成页可见", $("pageGen") && !$("pageGen").hidden);
  ok("创作页隐藏", $("pageCreate") ? $("pageCreate").hidden : true);

  const K = window.__genTest;
  ok("测试钩子存在", !!K, K ? Object.keys(K) : null);

  // ---------------- V2 默认态
  const seg = [...document.querySelectorAll("#genSeg .seg-item")];
  ok("段有两个", seg.length === 2, seg.length);
  ok("默认 V2 高亮", document.querySelector('#genSeg .seg-item[data-kind=v2]').classList.contains("is-active"));
  ok("V2 显示 x/y 尺寸", !$("genSizeXY").hidden);
  ok("V2 隐藏单尺寸", $("genSize").hidden);
  ok("V2 隐藏动作", $("genMotionWrap").hidden);
  ok("V2 显示 ai_expand", !$("genExpandWrap").hidden);
  ok("V2 价格 6", $("genCost").textContent.trim() === "6", $("genCost").textContent);
  ok("V2 原价 8", $("genCostOld").textContent.trim() === "8", $("genCostOld").textContent);
  const v2f = [...$("genFrames").options].map((o) => o.value);
  ok("V2 帧选项 16/20/24/32", JSON.stringify(v2f) === '["16","20","24","32"]', v2f);
  ok("V2 默认 24 帧", $("genFrames").value === "24", $("genFrames").value);

  // 高级选项
  ok("高级默认收起", $("genAdv").hidden === true);
  $("genAdvToggle").click();
  await wait(50);
  ok("高级可展开", $("genAdv").hidden === false);
  ok("高级里能看见噪声", !!$("genNoise"));

  // ---------------- V2 参数形状
  if (K) {
    $("genPad").checked = false;
    $("genPad").dispatchEvent(new Event("change"));
    await wait(50);
    const p = K.params();
    const keys = Object.keys(p).sort();
    ok("V2 恰 8 键", keys.length === 8, keys);
    ok("V2 键名正确",
      JSON.stringify(keys) === JSON.stringify(["ai_expand", "output_frames", "padding", "pixel_size", "prompt", "seed", "use_end_image", "use_mid_image"]),
      keys);
    ok("V2 pixel_size 是对象", p.pixel_size && typeof p.pixel_size.x === "number", p.pixel_size);
    ok("V2 padding 四边", p.padding && "left" in p.padding && "right" in p.padding && "top" in p.padding && "bottom" in p.padding, p.padding);
    ok("V2 无 bg_color", !("bg_color" in p));
    ok("V2 无 noise_strength", !("noise_strength" in p));
    ok("V2 无 color_count", !("color_count" in p));
    ok("V2 无 color_count2", !("quantization_colors" in p));
    ok("V2 output_frames 数字", typeof p.output_frames === "number", p.output_frames);
    ok("V2 seed 数字", typeof p.seed === "number" && p.seed >= 0, p.seed);
    ok("V2 有 ai_expand", typeof p.ai_expand === "boolean", p.ai_expand);

    // padding 参与 pixel_size
    $("genPad").checked = true;
    $("genPad").dispatchEvent(new Event("change"));
    await wait(50);
    ok("V2 留白显示四边输入", !$("genPadXYWrap").hidden);
    ok("V2 留白隐藏九宫格", $("genPadPosWrap").hidden);
    $("genPadLeft").value = "64";
    $("genPadRight").value = "64";
    $("genPadTop").value = "0";
    $("genPadBottom").value = "0";
    $("genPadLeft").dispatchEvent(new Event("change"));
    await wait(120);
    const p2 = K.params();
    ok("留白左右 64 → pixel_size.x = 128+128", p2.pixel_size.x === 256, p2.pixel_size);
    ok("留白 y 不变", p2.pixel_size.y === 128, p2.pixel_size);
    ok("padding 原样带上", p2.padding.left === 64 && p2.padding.right === 64, p2.padding);

    // 夹取：单边不得超过 256-尺寸
    const c = K.clampPad({ x: 128, y: 128 }, { left: 999, right: 999, top: 999, bottom: 999 });
    ok("留白被夹到 128 总计", c.left + c.right <= 128, c);

    // ---------------- 切 V1
    $("genPad").checked = false;
    $("genPad").dispatchEvent(new Event("change"));
    document.querySelector('#genSeg .seg-item[data-kind=v1]').click();
    await wait(120);
    ok("V1 显示单尺寸", !$("genSize").hidden);
    ok("V1 隐藏 x/y", $("genSizeXY").hidden);
    ok("V1 显示动作", !$("genMotionWrap").hidden);
    ok("V1 隐藏 ai_expand", $("genExpandWrap").hidden);
    ok("V1 显示噪声", !$("genNoiseWrap").hidden);
    ok("V1 价格 4", $("genCost").textContent.trim() === "4", $("genCost").textContent);
    ok("V1 原价 5", $("genCostOld").textContent.trim() === "5", $("genCostOld").textContent);
    const v1f = [...$("genFrames").options].map((o) => o.value);
    ok("V1 帧选项 8~16", JSON.stringify(v1f) === '["8","10","12","14","16"]', v1f);

    const q = K.params();
    ok("V1 pixel_size 字符串", typeof q.pixel_size === "string", q.pixel_size);
    ok("V1 length = 2*帧+1", q.length === 2 * +$("genFrames").value + 1, q.length);
    ok("V1 motion_type", typeof q.motion_type === "string", q.motion_type);
    ok("V1 attack_type", typeof q.attack_type === "string", q.attack_type);
    ok("V1 use_padding 布尔", typeof q.use_padding === "boolean", q.use_padding);
    ok("V1 padding_position", typeof q.padding_position === "string", q.padding_position);
    ok("V1 scale_factor 数字", typeof q.scale_factor === "number", q.scale_factor);
    ok("V1 128 无留白 scale = 6", q.scale_factor === 768 / 128, q.scale_factor);
    ok("V1 noise_strength", typeof q.noise_strength === "number", q.noise_strength);
    ok("V1 use_mid_image", typeof q.use_mid_image === "boolean", q.use_mid_image);
    ok("V1 无 padding 对象", !("padding" in q));
    ok("V1 无 output_frames", !("output_frames" in q));

    // 默认动作提示词
    $("genMotion").value = "walk";
    $("genMotion").dispatchEvent(new Event("change"));
    await wait(80);
    ok("切动作会带默认提示词", /walks forward/.test($("genPrompt").value), $("genPrompt").value);
    ok("walk → 12 帧", $("genFrames").value === "12", $("genFrames").value);
    ok("walk → 噪声 0.6", Math.abs(+$("genNoise").value - 0.6) < 1e-6, $("genNoise").value);

    // attack 三态
    $("genMotion").value = "attack";
    $("genMotion").dispatchEvent(new Event("change"));
    await wait(80);
    ok("attack 显示攻击类型", !$("genAttackWrap").hidden);
    ok("attack → 16 帧", $("genFrames").value === "16", $("genFrames").value);
    ok("melee → noise 0.6", Math.abs(+$("genNoise").value - 0.6) < 1e-6, $("genNoise").value);
    ok("melee → attack 预设", /powerful strike/.test($("genPrompt").value), $("genPrompt").value);

    $("genAttack").value = "ranged";
    $("genAttack").dispatchEvent(new Event("change"));
    await wait(80);
    ok("ranged 换成远程提示词", /bullet/.test($("genPrompt").value), $("genPrompt").value);
    ok("ranged → noise 0.3", Math.abs(+$("genNoise").value - 0.3) < 1e-6, $("genNoise").value);

    $("genAttack").value = "other";
    $("genAttack").dispatchEvent(new Event("change"));
    await wait(80);
    ok("other 换成占位提示词", $("genPrompt").value === "[describe the attack]", $("genPrompt").value);
    ok("other → noise 0.3", Math.abs(+$("genNoise").value - 0.3) < 1e-6, $("genNoise").value);

    // defeated → 16 帧
    $("genMotion").value = "defeated";
    $("genMotion").dispatchEvent(new Event("change"));
    await wait(80);
    ok("defeated → 16 帧", $("genFrames").value === "16", $("genFrames").value);
    ok("defeated 隐藏攻击类型", $("genAttackWrap").hidden);

    // 用户改过提示词就不再被覆盖
    $("genPrompt").value = "my own prompt";
    $("genPrompt").dispatchEvent(new Event("input"));
    $("genMotion").value = "idle";
    $("genMotion").dispatchEvent(new Event("change"));
    await wait(80);
    ok("手改提示词不被覆盖", $("genPrompt").value === "my own prompt", $("genPrompt").value);

    // 32 → scale 16
    $("genSize").value = "32";
    $("genSize").dispatchEvent(new Event("change"));
    await wait(80);
    const q32 = K.params();
    ok("V1 32 无留白 scale = 16", q32.scale_factor === 512 / 32, q32.scale_factor);

    // ---------------- 回 V2，测切片
    document.querySelector('#genSeg .seg-item[data-kind=v2]').click();
    await wait(120);
    const cv = document.createElement("canvas");
    cv.width = 24 * 32;
    cv.height = 32;
    const c2 = cv.getContext("2d");
    for (let i = 0; i < 24; i += 1) {
      c2.fillStyle = "hsl(" + i * 15 + ",80%,55%)";
      c2.fillRect(i * 32, 0, 32, 32);
    }
    const url = cv.toDataURL("image/png");
    const im = new Image();
    await new Promise((r) => { im.onload = r; im.src = url; });
    $("genResult").textContent = "";
    K.frames = 24;
    K.slice($("genResult"), im, url);
    await wait(100);
    const fr = document.querySelectorAll("#genResult .gen-strip .fr").length;
    ok("切出 24 帧", fr === 24, fr);
    ok("有播放区", !!document.querySelector("#genResult .gen-preview"));
    ok("播放区尺寸非零", (() => { const e = document.querySelector("#genResult .gen-preview"); return e && e.style.width && e.style.width !== "0px"; })());

    // 不可整除
    $("genResult").textContent = "";
    const cv2 = document.createElement("canvas");
    cv2.width = 100;
    cv2.height = 32;
    cv2.getContext("2d").fillRect(0, 0, 100, 32);
    const url2 = cv2.toDataURL("image/png");
    const im2 = new Image();
    await new Promise((r) => { im2.onload = r; im2.src = url2; });
    K.slice($("genResult"), im2, url2);
    await wait(60);
    ok("不可整除时不切帧", document.querySelectorAll("#genResult .gen-strip .fr").length === 0);
    ok("不可整除有说明", /没自动切帧/.test($("genResult").textContent), $("genResult").textContent.trim().slice(0, 80));

    // ---------------- V1 中间图 → 帧数锁 16 + 进请求体
    document.querySelector('#genSeg .seg-item[data-kind=v1]').click();
    await wait(120);
    $("genFrames").value = "10";
    const mid = await fetch(url).then((r) => r.blob());
    const midFile = new File([mid], "mid.png", { type: "image/png" });
    K.ingest(midFile, 1);
    await wait(600);
    ok("V1 中间图 → 16 帧", $("genFrames").value === "16", $("genFrames").value);
    ok("中间图进 refs[1]", !!K.state.refs[1]);
    ok("中间图有 base64", !!(K.state.refs[1] && K.state.refs[1].b64));
    const pv1 = K.params();
    ok("V1 use_mid_image = true", pv1.use_mid_image === true, pv1.use_mid_image);
    ok("V1 use_end_image = false", pv1.use_end_image === false, pv1.use_end_image);
    K.state.refs[1] = null;

    // ---------------- 拦截路径
    // V2 没有「留空用预设提示词」的兜底，空就是空；V1 会用动作预设填上（rika 行为）
    document.querySelector('#genSeg .seg-item[data-kind=v2]').click();
    await wait(120);
    ok("切回 V2 生效", K.state.kind === "v2", K.state.kind);
    K.state.refs[0] = { b64: "iVBORw0KGgo=", preview: url, img: im };
    $("genPrompt").value = "";
    const n0 = logN();
    await K.run();
    await wait(200);
    ok("空提示词被拦", logN() > n0 && /提示词/.test(lastLog()), lastLog());

    // 无参考图（有提示词）
    K.state.refs[0] = null;
    $("genPrompt").value = "test prompt";
    const n1 = logN();
    await K.run();
    await wait(150);
    ok("无参考图被拦", logN() > n1 && /起始/.test(lastLog()), lastLog());

    // 参考图「旋转 / 翻转」在空槽位上应有提示
    const n2 = logN();
    $("genRotate").click();
    await wait(60);
    ok("空槽点旋转有提示", logN() > n2 && /空的/.test(lastLog()), lastLog());
  }

  return out;
})()
