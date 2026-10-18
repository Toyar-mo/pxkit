// 生成页离线校验：headless Chrome + CDP，执行浏览器端断言。
// 不提交任务、不消耗积分。
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const URL = "http://127.0.0.1:8765/";
const PORT = 9333;

const PROBE = fs.readFileSync(path.join(__dirname, "verify_gen.probe.js"), "utf8");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function json(url) {
  const r = await fetch(url);
  return r.json();
}

async function main() {
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "pxgen-"));
  const chrome = spawn(CHROME, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--no-first-run",
    "--disable-extensions",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--window-size=1440,1000",
    `--user-data-dir=${prof}`,
    `--remote-debugging-port=${PORT}`,
    URL,
  ], { stdio: "ignore" });

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = await json(`http://127.0.0.1:${PORT}/json/version`); break; }
    catch { await sleep(250); }
  }
  if (!ver) { chrome.kill(); throw new Error("chrome 没起来"); }

  let page = null;
  for (let i = 0; i < 60; i++) {
    const list = await json(`http://127.0.0.1:${PORT}/json/list`);
    page = list.find((t) => t.type === "page" && t.url.startsWith("http://127.0.0.1:8765"));
    if (page) break;
    await sleep(250);
  }
  if (!page) { chrome.kill(); throw new Error("找不到目标页"); }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const logs = [];

  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.consoleAPICalled") {
      logs.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (m.method === "Runtime.exceptionThrown") {
      logs.push("EXC " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
    }
  });

  const send = (method, params) =>
    new Promise((res) => { const n = ++id; pending.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });

  await new Promise((r) => ws.addEventListener("open", r));
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
  });

  // 等应用初始化
  await sleep(1200);

  // 先拍「默认态」整页图（断言会改状态，所以要在前面拍）
  await send("Runtime.evaluate", {
    expression: `
      document.querySelector('[data-nav="gen"]').click();
      new Promise(r => setTimeout(r, 350));
    `,
    awaitPromise: true,
  });
  const metrics = await send("Page.getLayoutMetrics", {});
  const h = Math.min(4000, Math.ceil(metrics.result.cssContentSize.height));
  const shot = await send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1440, height: h, scale: 1 },
  });
  const shotPath = path.resolve(__dirname, "docs", "gen-page.png");
  if (shot.result?.data) fs.writeFileSync(shotPath, Buffer.from(shot.result.data, "base64"));

  const r = await send("Runtime.evaluate", {
    expression: PROBE,
    awaitPromise: true,
    returnByValue: true,
    timeout: 30000,
  });

  let out;
  if (r.result?.exceptionDetails) {
    out = { fatal: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text };
  } else {
    out = r.result?.result?.value;
  }

  ws.close();
  chrome.kill();

  console.log("=== console ===");
  logs.forEach((l) => console.log(l));
  console.log("=== result ===");
  console.log(JSON.stringify(out, null, 2));
  if (out && out.checks) {
    const bad = out.checks.filter((c) => !c.pass);
    console.log(`\n${out.checks.length - bad.length}/${out.checks.length} 通过`);
    if (bad.length) { console.log("失败项："); bad.forEach((b) => console.log("  ✗", b.name, JSON.stringify(b.extra))); }
    if (out.errors && out.errors.length) console.log("页面错误:", out.errors);
  }
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
