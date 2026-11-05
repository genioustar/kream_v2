// BRIDGE_PORT 는 이 파일을 붙이기 전에 정의된다.
// ego lite Node 의 net.listen 은 프로세스를 끝내고, 밖에서 만든 파일은 이 런타임이 못 본다.
// 그래서 Python 이 포트를 열고 여기서 접속한다.
const net = await import("node:net");

let running = true;
let task = null;
let page = null;
let lastDocument = null;
const requests = new Map();
const socket = net.connect(BRIDGE_PORT, "127.0.0.1");
let incoming = "";
const queue = [];

socket.on("data", (chunk) => {
  incoming += chunk.toString("utf8");
  let index = incoming.indexOf("\n");
  while (index >= 0) {
    const line = incoming.slice(0, index).trim();
    incoming = incoming.slice(index + 1);
    if (line) {
      queue.push(JSON.parse(line));
    }
    index = incoming.indexOf("\n");
  }
});

function send(payload) {
  try {
    socket.write(`${JSON.stringify(payload)}\n`);
  } catch (err) {
    const safe = { ...payload, body: null };
    try {
      socket.write(`${JSON.stringify(safe)}\n`);
    } catch {
      // 본문이 직렬화되지 않으면 그 이벤트만 버린다.
    }
  }
}

function resourceType(type) {
  const mapped = {
    Document: "document",
    XHR: "xhr",
    Fetch: "fetch",
    Image: "image",
    Script: "script",
    Stylesheet: "stylesheet",
    Font: "font",
    Media: "media",
    WebSocket: "websocket",
    Manifest: "manifest",
    Other: "other",
  };
  return mapped[type] || "other";
}

function isFunctionSource(source) {
  return /^\s*(async\s+)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>/.test(source)
    || /^\s*(async\s+)?function\b/.test(source);
}

function buildExpression(source, hasArg, arg) {
  if (!isFunctionSource(source)) {
    return source;
  }
  if (!hasArg) {
    return `(${source})()`;
  }
  return `(${source})(${JSON.stringify(arg)})`;
}

async function runtimeEvaluate(expression) {
  const result = await page.cdp("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "evaluate failed");
  }
  return result.result ? result.result.value : null;
}

async function drain() {
  if (!page) {
    return;
  }
  let events = [];
  try {
    events = await page.events();
  } catch {
    return;
  }
  for (const event of events) {
    if (event.method === "Network.requestWillBeSent") {
      const params = event.params;
      const type = resourceType(params.type);
      requests.set(params.requestId, {
        url: params.request.url,
        type,
        document: params.type === "Document",
      });
      send({
        event: true,
        kind: "request",
        url: params.request.url,
        resourceType: type,
        document: params.type === "Document",
      });
    } else if (event.method === "Network.responseReceived") {
      const params = event.params;
      const type = resourceType(params.type);
      let body = null;
      const url = params.response.url || "";
      if ((type === "xhr" || type === "fetch") && url.includes("api.nike.com/discover/product_wall")) {
        try {
          const got = await page.cdp("Network.getResponseBody", { requestId: params.requestId });
          body = got.base64Encoded
            ? Buffer.from(got.body, "base64").toString("utf8")
            : got.body;
          if (body && body.length > 500_000) {
            body = body.slice(0, 500_000);
          }
        } catch {
          body = null;
        }
      }
      const record = {
        event: true,
        kind: "response",
        url: params.response.url,
        status: params.response.status,
        headers: params.response.headers || {},
        resourceType: type,
        document: params.type === "Document",
        body,
      };
      if (params.type === "Document") {
        lastDocument = record;
      }
      send(record);
    } else if (event.method === "Network.loadingFailed") {
      const meta = requests.get(event.params.requestId) || {};
      send({
        event: true,
        kind: "requestfailed",
        url: meta.url || "",
        resourceType: meta.type || "other",
        document: Boolean(meta.document),
      });
    }
  }
}

async function handle(cmd) {
  const params = cmd.params || {};
  if (cmd.op === "open") {
    if (!task) {
      task = await taskSpace(params.name || "resell-sniper");
      page = task.page("p1");
      await page.cdp("Network.enable");
    }
    await drain();
    return { spaceId: task.spaceId, url: await page.url() };
  }
  if (cmd.op === "finish") {
    if (task) {
      await task.finish({ keep: [] });
      task = null;
      page = null;
    }
    running = false;
    return { closed: true };
  }
  if (!page) {
    throw new Error("Task Space 가 열려 있지 않습니다.");
  }
  if (cmd.op === "goto") {
    const timeout = params.timeout || 30_000;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("goto timeout")), timeout);
      page.goto(params.url).then(() => {
        clearTimeout(timer);
        resolve();
      }, (err) => {
        clearTimeout(timer);
        reject(err);
      });
    });
    lastDocument = null;
    await drain();
    return { url: await page.url(), title: await page.title(), response: lastDocument };
  }
  if (cmd.op === "url") {
    return { url: await page.url() };
  }
  if (cmd.op === "title") {
    return { title: await page.title() };
  }
  if (cmd.op === "evaluate") {
    const expression = buildExpression(params.source, params.hasArg === true, params.arg);
    return { value: await runtimeEvaluate(expression) };
  }
  if (cmd.op === "evaluate_all") {
    const css = String(params.selector || "").split(" >> nth=")[0];
    const expression = `(() => { const cards = Array.from(document.querySelectorAll(${JSON.stringify(css)})); const fn = (${params.source}); return fn(cards); })()`;
    return { value: await runtimeEvaluate(expression) };
  }
  if (cmd.op === "wait_for_selector") {
    await page.waitForSelector(params.selector, {
      state: params.state || "visible",
      timeout: params.timeout || 30_000,
    });
    return { url: await page.url() };
  }
  if (cmd.op === "wait_for_load_state") {
    await page.waitForLoadState(params.state || "load");
    return { url: await page.url() };
  }
  if (cmd.op === "content") {
    return { html: await runtimeEvaluate("document.documentElement ? document.documentElement.outerHTML : ''") };
  }
  if (cmd.op === "screenshot") {
    const options = { path: params.path };
    if (params.fullPage) {
      options.fullPage = true;
    }
    await page.screenshot(options);
    return { path: params.path };
  }
  if (cmd.op === "cookies") {
    const got = await page.cdp("Network.getCookies");
    return { cookies: (got.cookies || []).map((cookie) => ({ name: cookie.name })) };
  }
  if (cmd.op === "click") {
    await page.click(params.selector);
    await drain();
    return { url: await page.url() };
  }
  if (cmd.op === "fill") {
    await page.fill(params.selector, params.value);
    await drain();
    return { url: await page.url() };
  }
  if (cmd.op === "keyboard_type") {
    await page.keyboard.insertText(params.text);
    return { url: await page.url() };
  }
  if (cmd.op === "keyboard_press") {
    await page.keyboard.press(params.key);
    await drain();
    return { url: await page.url() };
  }
  if (cmd.op === "element_eval") {
    const expression = `(() => {
      const spec = ${JSON.stringify(params.spec)};
      function query(scope, node) {
        if (!node) return scope;
        const raw = String(node.selector || "");
        const css = raw.split(" >> nth=")[0];
        let index = node.index == null ? null : node.index;
        if (raw.includes(">> nth=")) index = Number(raw.split(" >> nth=")[1]);
        const list = scope.querySelectorAll(css);
        const el = list[index == null ? 0 : index];
        if (!el) return null;
        return node.child ? query(el, node.child) : el;
      }
      const el = query(document, spec);
      const fn = (${params.source});
      return ${params.hasArg ? "fn(el, " + JSON.stringify(params.arg) + ")" : "fn(el)"};
    })()`;
    return { value: await runtimeEvaluate(expression) };
  }
  throw new Error(`알 수 없는 명령: ${cmd.op}`);
}

await new Promise((resolve, reject) => {
  socket.once("connect", resolve);
  socket.once("error", reject);
});
send({ ready: true });

while (running) {
  if (queue.length === 0) {
    await drain();
    await new Promise((resolve) => setTimeout(resolve, 20));
    continue;
  }
  const cmd = queue.shift();
  try {
    const result = await handle(cmd);
    send({ id: cmd.id, ok: true, result });
  } catch (err) {
    send({ id: cmd.id, ok: false, error: err && err.message ? err.message : String(err) });
  }
}
socket.end();
