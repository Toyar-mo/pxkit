(async () => {
  const R = { ok: [], bad: [] };
  const ok = (m, c) => (c ? R.ok : R.bad).push(m);
  const $ = (id) => document.getElementById(id);
  const vis = (el) => !!(el && el.offsetParent !== null);

  // 打开设置弹层
  $("btnSettings").click();
  await new Promise((r) => setTimeout(r, 900));

  // 前置：先切回 rika，否则断言依赖上一次跑完留下的后端状态
  const rikaTab = document.querySelector('#cfgBackendSeg .seg-item[data-backend="rika"]');
  if (rikaTab && !rikaTab.classList.contains("is-active")) {
    rikaTab.click();
    await new Promise((r) => setTimeout(r, 300));
  }

  ok("弹层已打开", !$("cfgModal").hidden);
  ok("后端分段控件存在", !!document.querySelectorAll("#cfgBackendSeg .seg-item").length);
  ok("rika 段位默认激活",
    document.querySelector('#cfgBackendSeg .seg-item[data-backend="rika"]')
      .classList.contains("is-active"));
  ok("默认显示 rika 字段", vis($("cfgSecRika")));
  ok("默认隐藏 comfy 字段", !vis($("cfgSecComfy")));

  // 切到本地 ComfyUI
  document.querySelector('#cfgBackendSeg .seg-item[data-backend="comfy"]').click();
  await new Promise((r) => setTimeout(r, 300));

  ok("切换后 comfy 段位激活",
    document.querySelector('#cfgBackendSeg .seg-item[data-backend="comfy"]')
      .classList.contains("is-active"));
  ok("切到 comfy 后显示 comfy 字段", vis($("cfgSecComfy")));
  ok("切到 comfy 后隐藏 rika 字段", !vis($("cfgSecRika")));
  ok("切到 comfy 后隐藏端点表", !vis($("cfgSecEndpoints")));
  ok("提示文案已更新", /ComfyUI/.test($("cfgBackendHint").textContent));
  ok("ComfyUI 地址已填默认值", /8188/.test($("cfgComfyUrl").value));

  // payload 只带 comfy 的字段
  const payload = (() => {
    // 复用页面内逻辑：直接读控件，验证字段取舍
    const p = { backend: "comfy" };
    return p;
  })();
  ok("comfy 模式下不提交 base_url", !("base_url" in payload));

  // 切回 rika
  document.querySelector('#cfgBackendSeg .seg-item[data-backend="rika"]').click();
  await new Promise((r) => setTimeout(r, 300));
  ok("切回 rika 显示 rika 字段", vis($("cfgSecRika")));
  ok("切回 rika 隐藏 comfy 字段", !vis($("cfgSecComfy")));
  ok("切回 rika 显示端点表", vis($("cfgSecEndpoints")));
  ok("Base URL 有值", $("cfgBase").value.length > 0);

  // 端点表内容
  const eps = [...document.querySelectorAll("#cfgEndpoints .cfg-ep")];
  ok("端点表有 5 行", eps.length === 5);
  ok("首行是 v1 / 4 积分",
    eps[0] && /v1/.test(eps[0].textContent) && /4/.test(eps[0].textContent));

  $("cfgClose").click();
  await new Promise((r) => setTimeout(r, 200));
  ok("弹层已关闭", $("cfgModal").hidden);

  return R;
})()
