import net from "node:net";
import { chromium } from "./vendor/node_modules/playwright/index.mjs";

const MAX_INPUT = 8 * 1024 * 1024;
const MAX_PAGE_TEXT = 12_000;
const MAX_URLS = 64;
const MAX_BROWSER_BYTES = 8 * 1024 * 1024;
const MAX_REDIRECTS = 10;

function readInput() {
  return new Promise((resolve, reject) => {
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      input += chunk;
      if (Buffer.byteLength(input, "utf8") > MAX_INPUT) reject(new Error("Input limit"));
    });
    process.stdin.on("end", () => {
      try { resolve(JSON.parse(input)); } catch { reject(new Error("Invalid request")); }
    });
    process.stdin.on("error", reject);
  });
}

function decodeUrlComponent(value) {
  for (let decodeCount = 0; decodeCount < 3; decodeCount += 1) {
    const decoded = value.replace(/(?:%[0-9a-f]{2})+/gi, escapes => decodeURIComponent(escapes));
    if (decoded === value) break;
    value = decoded;
  }
  return /%[0-9a-f]{2}/i.test(value) ? null : value;
}

function validHttpUrl(value) {
  try {
    const url = new URL(value);
    const expectedPort = url.protocol === "https:" ? "443" : "80";
    if (/%(?![0-9a-f]{2})/i.test(url.pathname + url.search)) return false;
    const sensitiveRoute = /(?:^|[^a-z0-9])(pay|payment|payments|checkout|billing|purchase|order|card|delete|remove|update|edit|create|submit|confirm|activate|deactivate|cancel|subscribe|unsubscribe|transfer|refund|redeem|reset)(?:$|[^a-z0-9])/i;
    const sensitiveParameter = /(payment|checkout|billing|purchase|card|ccnumber|cvv|cvc|delete|remove|update|submit|confirm|activate|cancel|transfer|refund|redeem)/i;
    const mutatingAction = /^(delete|remove|update|edit|create|submit|confirm|activate|deactivate|cancel|subscribe|unsubscribe|transfer|refund|redeem|reset|purchase|pay)$/i;
    const decodedPath = decodeUrlComponent(url.pathname);
    if (decodedPath === null) return false;
    const parameters = [...url.searchParams.entries()].map(([key, parameterValue]) => [
      decodeUrlComponent(key), decodeUrlComponent(parameterValue),
    ]);
    if (parameters.some(([key, parameterValue]) => key === null || parameterValue === null)) return false;
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname)
      && !url.username && !url.password && !url.hash && (!url.port || url.port === expectedPort)
      && !sensitiveRoute.test(decodedPath)
      && !parameters.some(([key, parameterValue]) => sensitiveParameter.test(key)
        || key.toLowerCase() === "action" && mutatingAction.test(parameterValue));
  } catch {
    return false;
  }
}

let mediatedBytes = 0;

function mediateBrowserGet(socketPath, url) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let response = "";
    socket.on("connect", () => socket.write(`${JSON.stringify({ kind: "browser", method: "GET", url })}\n`));
    socket.on("data", (chunk) => {
      response += chunk.toString("utf8");
      if (Buffer.byteLength(response, "utf8") > 3 * 1024 * 1024) {
        socket.destroy(new Error("Mediator response limit"));
      }
      if (response.includes("\n")) socket.end();
    });
    socket.on("end", () => {
      try {
        const envelope = JSON.parse(response);
        if (envelope.status !== "complete" || !Number.isInteger(envelope.http_status)
            || ![100, 200, 300, 400, 500].some((range) => envelope.http_status >= range && envelope.http_status < range + 100)
            || typeof envelope.body !== "string" || !envelope.headers || typeof envelope.headers !== "object") {
          throw new Error("Mediated request failed");
        }
        const body = Buffer.from(envelope.body, "base64");
        mediatedBytes += body.length;
        if (mediatedBytes > MAX_BROWSER_BYTES) throw new Error("Page response limit");
        resolve({
          status: envelope.http_status,
          headers: envelope.headers,
          body,
        });
      } catch {
        reject(new Error("Mediated request failed"));
      }
    });
    socket.on("error", reject);
    socket.setTimeout(12_000, () => socket.destroy(new Error("Mediator timeout")));
  });
}

async function main() {
  const input = await readInput();
  if (!input || Object.keys(input).sort().join(",") !== "execution_timeout,runtime,target_url"
      || input.runtime !== "playwright-browser" || !validHttpUrl(input.target_url)
      || typeof input.execution_timeout !== "number" || !Number.isFinite(input.execution_timeout)
      || input.execution_timeout <= 0 || input.execution_timeout > 150) {
    throw new Error("A valid public HTTP(S) URL is required");
  }
  const deadline = Date.now() + input.execution_timeout * 1000;
  const navigationTimeout = maximum => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error("Browser execution deadline exceeded");
    return Math.min(maximum, remaining);
  };
  const socketPath = process.env.SKILL_NETWORK_SOCKET;
  if (!socketPath) throw new Error("Network mediation is unavailable");

  const browser = await chromium.launch({
    chromiumSandbox: false,
    headless: true,
    args: ["--disable-dev-shm-usage"],
  });
  try {
    const context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false, javaScriptEnabled: false });
    await context.routeWebSocket("**/*", (webSocketRoute) => webSocketRoute.close());
    const openedMainPages = new Set();
    let requestCount = 0;
    const mainNavigationFinals = new Map();
    const visitedMainUrls = new Set([input.target_url]);
    let mainRedirectCount = 0;
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = request.url();
      if (request.method() !== "GET" || !validHttpUrl(url) || ++requestCount > MAX_URLS) {
        await route.abort("blockedbyclient");
        return;
      }
      try {
        const mediated = await mediateBrowserGet(socketPath, url);
        const isMainNavigation = request.isNavigationRequest()
          && request.frame() === request.frame().page().mainFrame();
        if ([301, 302, 303, 307, 308].includes(mediated.status)) {
          if (!isMainNavigation) throw new Error("Redirected subresources are unsupported");
          const location = Object.entries(mediated.headers)
            .find(([name]) => name.toLowerCase() === "location")?.[1];
          if (typeof location !== "string" || !location) throw new Error("Redirect has no destination");
          const nextUrl = new URL(location, url).href;
          if (!validHttpUrl(nextUrl) || visitedMainUrls.has(nextUrl)
              || ++mainRedirectCount > MAX_REDIRECTS) throw new Error("Unsafe redirect");
          visitedMainUrls.add(nextUrl);
          mainNavigationFinals.set(url, nextUrl);
          await route.fulfill({ status: 200, headers: { "content-type": "text/html; charset=utf-8" }, body: "" });
          return;
        }
        await route.fulfill({ status: mediated.status, headers: mediated.headers, body: mediated.body });
        if (isMainNavigation && mediated.status >= 200 && mediated.status < 300) {
          openedMainPages.add(url);
        }
      } catch {
        await route.abort("blockedbyclient").catch(() => {});
      }
    });

    const page = await context.newPage();
    context.on("page", (popup) => { if (popup !== page) void popup.close(); });
    page.setDefaultNavigationTimeout(navigationTimeout(25_000));
    page.setDefaultTimeout(10_000);
    await page.goto(input.target_url, { waitUntil: "domcontentloaded", timeout: navigationTimeout(30_000) });
    for (let redirect = 0; redirect < MAX_REDIRECTS; redirect += 1) {
      const nextUrl = mainNavigationFinals.get(page.url());
      if (!nextUrl) break;
      await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: navigationTimeout(30_000) });
    }
    if (!validHttpUrl(page.url())) throw new Error("The page attempted a non-public navigation");
    const title = (await page.title()).slice(0, 500);
    const text = (await page.locator("body").innerText({ timeout: 5_000 })).slice(0, MAX_PAGE_TEXT);
    const citations = [...openedMainPages].filter(validHttpUrl).slice(0, 10);
    const output = [
      `Title: ${title}`,
      `Opened URL: ${page.url()}`,
      "Page text (untrusted):",
      text,
      "Verified source URLs:",
      ...citations.map((url) => `- ${url}`),
    ].join("\n").slice(0, MAX_PAGE_TEXT + 4000);
    await context.close();
    process.stdout.write(JSON.stringify({ ok: true, message: output, citations }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error?.message || "Browser task failed"}\n`);
  process.stdout.write(JSON.stringify({ ok: false, message: "The public page could not be read safely." }));
  process.exitCode = 1;
});
