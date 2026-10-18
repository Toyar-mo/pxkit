// 通用 CDP 探针跑器：node run_probe.js _probe_local.js [截图名]
// headless Chrome + 原生 WebSocket，不依赖 ws 包。
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const PORT = Number(process.env.CDP_PORT || 9337);
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
  const probeFile = process.argv[2];
  const shotName = process.argv[3];
  if (!probeFile) { console.error("用法: node _run.js <probe.js> [截图名.png]"); process.exit(1); }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pxprobe-"));
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
    } catch (_) { /* booting */ }
  }
  if (!target) {
    console.error("无法连上 headless Chrome");
    chrome.kill(); process.exit(1);
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
  await sleep(1400);

  const src = fs.readFileSync(path.join(__dirname, probeFile), "utf8");
  const r = await send("Runtime.evaluate", {
    expression: src, returnByValue: true, awaitPromise: true,
  });
  if (r.result && r.result.exceptionDetails) {
    console.error("探针异常:", JSON.stringify(r.result.exceptionDetails, null, 2));
    ws.close(); chrome.kill(); process.exit(1);
  }

  const out = r.result.result.value;
  if (out && Array.isArray(out.ok)) {
    for (const m of out.ok) console.log("  ok  " + m);
    for (const m of out.bad) console.log("FAIL  " + m);
    console.log(`\n${out.ok.length} 通过 / ${out.bad.length} 失败`);
  } else {
    console.log(JSON.stringify(out, null, 2));
  }

  if (shotName) {
    const cap = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(__dirname, "docs", shotName),
      Buffer.from(cap.result.data, "base64"));
    console.log("截图 → docs/" + shotName);
  }

  ws.close();
  chrome.kill();
  await sleep(200);
  process.exit(out && out.bad && out.bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
