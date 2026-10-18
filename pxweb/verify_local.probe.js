(async () => {
  const R = { ok: [], bad: [] };
  const ok = (m, c) => (c ? R.ok : R.bad).push(m);
  const $ = (id) => document.getElementById(id);
  const vis = (el) => !!(el && el.offsetParent !== null);

  await new Promise((r) => setTimeout(r, 1200));   // 等 caps / credits 落地

  // 前置：确保当前后端是本地 ComfyUI（不然下面所有断言都无意义）
  if (!window.__genTest.isFree()) {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend: "comfy" }),
    });
    await window.__genTest.loadCaps();
    await window.__genTest.loadCredits();
    await new Promise((r) => setTimeout(r, 600));
  }
  ok("已切到本地后端", window.__genTest.isFree());
  ok("已识别本地后端", document.body.textContent.includes("本地出图"));
  ok("隐藏了积分数字", !vis($("genCostWrap")));
  ok("隐藏了余额区", !vis($("genCreditsWrap")));
  ok("提示文案说明可纯文生图", /文生图/.test($("genKindHint").textContent));

  // 无参考图也能提交 —— 拦的是提示词，不是参考图
  $("genPrompt").value = "";
  $("genRun").click();
  await new Promise((r) => setTimeout(r, 400));
  const logs = [...document.querySelectorAll("#logBody .ln")].map((e) => e.textContent);
  const joined = logs.join("\n");
  ok("空提示词被拦", /提示词不能为空/.test(joined));
  ok("不再要求参考图", !/至少一张输入图/.test(joined));

  // 起点确实没有图
  ok("当前没有参考图", window.__genTest.state.refs.every((r) => !r));

  // 有提示词时应进入提交
  $("genPrompt").value = "a blue armored soldier running, pixel art, side view";
  $("genRun").click();
  await new Promise((r) => setTimeout(r, 600));
  const after = [...document.querySelectorAll("#logBody .ln")].map((e) => e.textContent).join("\n");
  ok("有提示词后开始提交", /提交 V2/.test(after));
  ok("日志不再提积分", !/6 积分/.test(after));
  ok("提交中按钮已禁用", $("genRun").disabled || /生成中|排队|提交中/.test($("genRunLabel").textContent));

  return R;
})()
