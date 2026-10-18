// 验证设置弹层的后端切换：切到 comfy 时该显示 ComfyUI 字段、藏起 rika 字段。
// 用 node 内置 WebSocket（Node 22+），不依赖 ws 包。
const fs = require("fs");
const http = require("http");
const path = require("path");
const { spawn } = require("child_process");

const PORT = 9335;
const PAGE = "http://127.0.0.1:8765/";
const CHROME = process.env.CHROME ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function listTargets() {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port: PORT, path: "/json/list" }, (r) => {
      let b = "";
      r.on("data", (d) => (b += d));
      r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on("error", rej);
  });
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "pxcfg-"));
  const chrome = spawn(CHROME, [
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${tmp}`,
    "--headless=new", "--disable-gpu", "--no-first-run",
    "--window-size=1440,1000", PAGE,
  ], { stdio: "ignore" });

  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(250);
    try {
      target = (await listTargets()).find(
        (t) => t.type === "page" && t.webSocketDebuggerUrl);
    } catch (_) { /* chrome still booting */ }
  }
  if (!target) {
    console.error("无法连上 headless Chrome");
    chrome.kill();
    process.exit(1);
  }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params) => new Promise((res) => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
  });
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  await send("Runtime.enable");
  await sleep(1200);

  const PROBE = fs.readFileSync(path.join(__dirname, "verify_cfg.probe.js"), "utf8");
  const r = await send("Runtime.evaluate", {
    expression: PROBE, returnByValue: true, awaitPromise: true,
  });
  if (r.result && r.result.exceptionDetails) {
    console.error("探针异常:", JSON.stringify(r.result.exceptionDetails, null, 2));
    ws.close(); chrome.kill(); process.exit(1);
  }

  const out = r.result.result.value;
  for (const m of out.ok) console.log("  ok  " + m);
  for (const m of out.bad) console.log("FAIL  " + m);
  console.log(`\n${out.ok.length} 通过 / ${out.bad.length} 失败`);

  ws.close();
  // 别让 chrome 子进程活着；Windows 下 kill 可能不彻底，用 taskkill 兜底
  chrome.kill();
  await sleep(200);
  process.exit(out.bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
