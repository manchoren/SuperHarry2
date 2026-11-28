import net from "node:net";
import { TextDecoder } from "node:util";
import { chromium } from "./vendor/node_modules/playwright/index.mjs";

const MAX_INPUT = 8 * 1024 * 1024;
const MAX_PAGE_TEXT = 12_000;
const MAX_SEARCH_MESSAGE_BYTES = 16 * 1024;
const MAX_LINK_DESTINATIONS = 5;
const MAX_LINKS_PER_PAGE = 20;
const LINK_STOP_WORDS = new Set(["about", "and", "are", "for", "from", "into", "more", "the", "this", "with"]);
const MAX_URLS = 64;
const MAX_BROWSER_BYTES = 8 * 1024 * 1024;
const MAX_REDIRECTS = 10;
const MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024;
const MAX_SESSION_TTL_MS = 120_000;
let failureStage = "input";

function readInputLine() {
  return new Promise((resolve, reject) => {
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      input += chunk;
      if (Buffer.byteLength(input, "utf8") > MAX_INPUT) reject(new Error("Input limit"));
      const end = input.indexOf("\n");
      if (end !== -1) resolve(JSON.parse(input.slice(0, end)));
    });
    process.stdin.on("end", () => {
      if (!input) reject(new Error("Missing browser session input"));
    });
    process.stdin.on("error", reject);
  });
}

function decodeUrlComponent(value) {
  try {
    for (let decodeCount = 0; decodeCount < 3; decodeCount += 1) {
      const decoded = value.replace(/(?:%[0-9a-f]{2})+/gi, (escapes) => decodeURIComponent(escapes));
      if (decoded === value) break;
      value = decoded;
    }
    return /%[0-9a-f]{2}/i.test(value) ? null : value;
  } catch {
    return null;
  }
}

function validHttpUrl(value, allowActionRoutes = false) {
  try {
    const url = new URL(value);
    const expectedPort = url.protocol === "https:" ? "443" : "80";
    if (/%(?![0-9a-f]{2})/i.test(url.pathname + url.search)) return false;
    const prohibitedRoute = /(?:^|[^a-z0-9])(pay|payment|payments|checkout|billing|purchase|order|card|transfer|refund|redeem|donate|subscription|wallet|iban|swift|routing|bank|wire|amount|total|currency|price)(?:$|[^a-z0-9])/i;
    const actionRoute = /(?:^|[^a-z0-9])(delete|remove|update|edit|create|submit|confirm|activate|deactivate|cancel|subscribe|unsubscribe|reset)(?:$|[^a-z0-9])/i;
    const prohibitedParameter = /(payment|checkout|billing|purchase|order|card|ccnumber|cvv|cvc|transfer|refund|redeem|donate|subscription|wallet|iban|swift|routing|bank|wire|amount|total|currency|price)/i;
    const actionParameter = /(delete|remove|update|edit|create|submit|confirm|activate|cancel|reset)/i;
    const mutatingAction = /^(delete|remove|update|edit|create|submit|confirm|activate|deactivate|cancel|subscribe|unsubscribe|transfer|refund|redeem|reset|purchase|pay)$/i;
    const decodedPath = decodeUrlComponent(url.pathname);
    if (decodedPath === null) return false;
    const parameters = [...url.searchParams.entries()].map(([key, parameterValue]) => [
      decodeUrlComponent(key), decodeUrlComponent(parameterValue),
    ]);
    if (parameters.some(([key, parameterValue]) => key === null || parameterValue === null)) return false;
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname)
      && !url.username && !url.password && !url.hash && (!url.port || url.port === expectedPort)
      && !prohibitedRoute.test(decodedPath)
      && (allowActionRoutes || !actionRoute.test(decodedPath))
      && !parameters.some(([key, parameterValue]) => prohibitedParameter.test(key)
        || key.toLowerCase() === "action" && mutatingAction.test(parameterValue)
        || !allowActionRoutes && actionParameter.test(key));
  } catch {
    return false;
  }
}

async function readOwnerActionContext(page, coordinates = null) {
  return page.evaluate(({ x, y, key }) => {
    const target = Number.isInteger(x) && Number.isInteger(y)
      ? document.elementFromPoint(x, y) : document.activeElement;
    const form = target?.closest?.("form") || target?.form || null;
    const button = target?.closest?.('button, input[type="submit"], input[type="image"], [role="button"]') || null;
    const link = target?.closest?.("a[href]") || null;
    const clean = value => String(value || "").replace(/\s+/g, " ").trim();
    const formAction = Boolean(form && (key || button && (button.form === form
      || button.type === "submit" || button.getAttribute("role") === "button")));
    const kind = formAction ? "form" : button ? "button" : link ? "link" : "other";
    const summary = clean(button?.innerText || button?.value || button?.getAttribute("aria-label")
      || link?.innerText || link?.getAttribute("aria-label") || target?.innerText || form?.getAttribute("aria-label"));
    const formText = form ? [...form.querySelectorAll("label, legend, h1, h2, h3")]
      .map(item => clean(item.innerText || item.textContent)).filter(Boolean).join(" | ") : "";
    const visible = element => {
      if (!element || !element.getClientRects().length || element.closest('[aria-hidden="true"]')) return false;
      let current = element;
      while (current && current !== document.documentElement) {
        const style = getComputedStyle(current);
        if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) <= 0.05
            || style.pointerEvents === "none") return false;
        current = current.parentElement;
      }
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const visibleFields = form ? [...form.querySelectorAll("input, textarea, select")]
      .filter(field => visible(field) && !field.disabled && (field.type || "").toLowerCase() !== "hidden")
      .map(field => field.name || "").filter(Boolean).slice(0, 33) : [];
    return {
      kind, summary: summary.slice(0, 300), form_text: formText.slice(0, 2000),
      visible_fields: visibleFields,
    };
  }, coordinates || { x: null, y: null, key: false });
}

async function readAgentActionForms(page) {
  return page.evaluate(() => {
    const clean = value => String(value || "").replace(/\s+/g, " ").trim();
    const visible = element => {
      if (!element || !element.getClientRects().length || element.closest('[aria-hidden="true"]')) return false;
      let current = element;
      while (current && current !== document.documentElement) {
        const style = getComputedStyle(current);
        if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) <= 0.05
            || style.pointerEvents === "none") return false;
        current = current.parentElement;
      }
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    return [...document.forms].filter(form => visible(form)).slice(0, 8).map((form, index) => {
      const submitters = [...form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]')];
      const fields = [...form.querySelectorAll("input, textarea, select")]
        .filter(field => !["hidden", "submit", "button", "reset", "image"].includes((field.type || "").toLowerCase()))
        .filter(field => visible(field) && !field.disabled)
        .slice(0, 32).map(field => ({
          name: field.name || "",
          type: field.tagName.toLowerCase() === "textarea" ? "textarea" : (field.type || field.tagName).toLowerCase(),
          label: clean([...(field.labels || [])].map(label => label.innerText || label.textContent).join(" ")
            || field.getAttribute("aria-label") || field.getAttribute("placeholder") || field.name),
          required: Boolean(field.required),
          options: field.tagName.toLowerCase() === "select"
            ? [...field.options].slice(0, 20).map(option => clean(option.label || option.textContent)) : [],
        }));
      const formText = [...form.querySelectorAll("label, legend, h1, h2, h3")]
        .map(item => clean(item.innerText || item.textContent)).filter(Boolean).join(" | ");
      return {
        index, url: form.action || location.href, method: (form.method || "GET").toUpperCase(),
        summary: clean(submitters.length === 1
          ? submitters[0].innerText || submitters[0].value || submitters[0].getAttribute("aria-label") : ""),
        form_text: formText.slice(0, 2000), fields,
      };
    });
  });
}

async function publishOwnerFrame(page, socketPath) {
  const frame = await page.screenshot({ type: "jpeg", quality: 65, fullPage: false });
  if (frame.length > MAX_SCREENSHOT_BYTES) throw new Error("Owner-control frame limit");
  const sent = await hostRequest(socketPath, {
    operation: "publish_owner_view", mime: "image/jpeg", data: frame.toString("base64"),
  });
  if (sent.status < 200 || sent.status >= 300) throw new Error("Owner-control frame unavailable");
}

async function prepareAgentAction(page, socketPath) {
  const forms = await readAgentActionForms(page);
  if (!forms.length) throw new Error("No visible form is available for agent preparation");
  const response = await hostRequest(socketPath, { operation: "prepare_action", forms });
  if (response.status !== 200) throw new Error("The requested form could not be prepared safely");
  let envelope;
  try { envelope = JSON.parse(response.body.toString("utf8")); }
  catch { throw new Error("Invalid agent action plan"); }
  const plan = envelope?.plan;
  const formDescription = forms[plan?.form_index];
  if (!plan || Object.keys(plan).sort().join(",") !== "fields,form_index,form_text,method,summary,url"
      || !Number.isInteger(plan.form_index) || !formDescription || plan.method !== "POST"
      || plan.url !== formDescription.url || !validHttpUrl(plan.url, true)
      || typeof plan.summary !== "string" || !Array.isArray(plan.fields) || !plan.fields.length) {
    throw new Error("The agent action plan did not match the visible form");
  }
  const form = page.locator("form:visible").nth(plan.form_index);
  const supportedTypes = new Set(["text", "email", "url", "search", "tel", "textarea"]);
  for (const item of plan.fields) {
    if (!item || Object.keys(item).sort().join(",") !== "name,value"
        || typeof item.name !== "string" || typeof item.value !== "string" || !item.value.trim()) {
      throw new Error("The agent action plan contained invalid form values");
    }
    const field = form.locator(`[name="${item.name.replace(/[\\"\]]/g, "\\$&")}"]`);
    if (await field.count() !== 1) throw new Error("The agent action did not match one visible field");
    const type = await field.evaluate(element => element.tagName.toLowerCase() === "textarea"
      ? "textarea" : (element.type || element.tagName).toLowerCase());
    if (!supportedTypes.has(type)) throw new Error("The agent action selected an unsupported form field");
    await field.fill(item.value);
  }
  const submitter = form.locator('button[type="submit"], button:not([type]), input[type="submit"]');
  if (await submitter.count() !== 1) throw new Error("The form does not have one clear submit control");
  const actionContext = {
    kind: "form", summary: plan.summary, form_text: plan.form_text,
    visible_fields: formDescription.fields.map(field => field.name), expected_url: plan.url,
  };
  await publishOwnerFrame(page, socketPath);
  return { form, submitter, actionContext };
}

function publicCitation(value) {
  try {
    const url = new URL(value);
    url.search = "";
    url.hash = "";
    return validHttpUrl(url.href) ? url.href : null;
  } catch { return null; }
}

function redactOwnerText(value, typedValues) {
  let result = value;
  for (const secret of [...typedValues].sort((left, right) => right.length - left.length)) {
    for (const variant of new Set([secret, encodeURIComponent(secret), encodeURI(secret)])) {
      if (!variant) continue;
      result = result.replace(new RegExp(variant.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), "[redacted]");
    }
  }
  return result;
}

function hostRequest(socketPath, action, timeoutMs = 12_000) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let response = "";
    socket.on("connect", () => socket.write(`${JSON.stringify({ kind: "browser", ...action })}\n`));
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
            || !envelope.headers || typeof envelope.headers !== "object" || typeof envelope.body !== "string"
            || typeof envelope.reviewed_action !== "boolean") {
          throw new Error("Browser control request failed");
        }
        const body = Buffer.from(envelope.body, "base64");
        if (body.length > 2 * 1024 * 1024) throw new Error("Browser control response limit");
        resolve({ status: envelope.http_status, headers: envelope.headers, body,
          reviewedAction: envelope.reviewed_action === true });
      } catch (error) {
        reject(error);
      }
    });
    socket.on("error", reject);
    socket.setTimeout(timeoutMs, () => socket.destroy(new Error("Mediator timeout")));
  });
}

function truncateUtf8(value, maxBytes) {
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length <= maxBytes) return value;
  const ellipsis = "\u2026";
  const prefixLimit = maxBytes - Buffer.byteLength(ellipsis, "utf8");
  for (let length = prefixLimit; length > prefixLimit - 4; length -= 1) {
    try {
      const prefix = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length));
      return `${prefix}${ellipsis}`;
    } catch { /* Drop an incomplete trailing UTF-8 sequence. */ }
  }
  throw new Error("Search page text could not be encoded safely");
}

async function installContextCookie(context, sourceUrl, raw) {
  if (typeof raw !== "string" || raw.length > 4096 || /[\r\n\0]/.test(raw)) throw new Error("Invalid cookie");
  const [pair, ...attributes] = raw.split(";");
  const separator = pair.indexOf("=");
  if (separator < 1) throw new Error("Invalid cookie");
  const name = pair.slice(0, separator).trim();
  const value = pair.slice(separator + 1).trim();
  if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || /[\x00-\x20;,]/.test(value)) throw new Error("Invalid cookie");
  const url = new URL(sourceUrl);
  const cookie = { name, value, sameSite: "Lax" };
  let cookieDomain = null;
  let cookiePath = null;
  let maxAge = null;
  let expires = null;
  for (const attribute of attributes) {
    const [rawName, ...parts] = attribute.trim().split("=");
    const key = rawName.toLowerCase();
    const parameter = parts.join("=").trim();
    if (key === "secure") cookie.secure = true;
    else if (key === "httponly") cookie.httpOnly = true;
    else if (key === "path" && parameter.startsWith("/")) cookiePath = parameter.slice(0, 1024);
    else if (key === "domain") {
      const domain = parameter.replace(/^\./, "").toLowerCase();
      if (domain !== url.hostname.toLowerCase()) throw new Error("Cross-site cookie blocked");
      cookieDomain = domain;
    } else if (key === "samesite") {
      const sameSite = parameter.toLowerCase();
      if (!["lax", "strict", "none"].includes(sameSite)) throw new Error("Invalid cookie policy");
      cookie.sameSite = sameSite[0].toUpperCase() + sameSite.slice(1);
    } else if (key === "max-age" && /^-?[0-9]+$/.test(parameter)) {
      maxAge = Math.max(-1, Math.min(31_536_000, Number(parameter)));
    } else if (key === "expires") {
      const expiry = Date.parse(parameter);
      if (Number.isFinite(expiry)) expires = Math.floor(expiry / 1000);
    }
  }
  if (cookieDomain) {
    cookie.domain = cookieDomain;
    cookie.path = cookiePath ?? "/";
  } else {
    const pathname = url.pathname;
    const defaultPath = pathname.lastIndexOf("/") <= 0 ? "/" : pathname.slice(0, pathname.lastIndexOf("/"));
    if (cookiePath !== null && cookiePath !== defaultPath) throw new Error("Unsupported host-only cookie path");
    cookie.url = sourceUrl;
  }
  if (maxAge !== null) cookie.expires = Math.floor(Date.now() / 1000) + maxAge;
  else if (expires !== null) cookie.expires = expires;
  if (cookie.sameSite === "None" && !cookie.secure) throw new Error("Invalid cookie policy");
  await context.addCookies([cookie]);
}

async function main() {
  failureStage = "setup";
  const input = await readInputLine();
  const browserSearch = input?.browser_search === true;
  const followPublicLinks = input?.follow_public_links === true;
  const followAuthenticatedLinks = input?.follow_authenticated_links === true;
  const hasActionRequest = typeof input?.action_request === "string";
  const baseInputKeys = ["browser_handoff", "browser_session", "execution_timeout", "runtime",
    ...(browserSearch ? ["browser_search", "target_urls"] : ["target_url"]),
    ...(followPublicLinks ? ["follow_public_links"] : []),
    ...(followAuthenticatedLinks ? ["follow_authenticated_links"] : []),
    ...(hasActionRequest ? ["action_request"] : [])];
  const expectedInputKeys = baseInputKeys.sort().join(",");
  const targetUrls = browserSearch ? input?.target_urls : [input?.target_url];
  if (!input || Object.keys(input).sort().join(",") !== expectedInputKeys
      || input.runtime !== "playwright-browser"
      || !Array.isArray(targetUrls) || (browserSearch ? targetUrls.length < 1 || targetUrls.length > 5 : targetUrls.length !== 1)
      || targetUrls.some((url) => !validHttpUrl(url))
      || browserSearch && input.browser_handoff !== false
      || followPublicLinks && (browserSearch || input.browser_handoff || hasActionRequest)
      || followAuthenticatedLinks && (!input.browser_handoff || browserSearch || followPublicLinks)
      || hasActionRequest && (!input.browser_handoff || browserSearch || !input.action_request.trim() || input.action_request.length > 1000)
      || !hasActionRequest && input?.action_request !== undefined
      || input.browser_handoff === true && targetUrls.some((url) => new URL(url).protocol !== "https:")
      || input.browser_session !== true
      || typeof input.browser_handoff !== "boolean"
      || typeof input.execution_timeout !== "number" || input.execution_timeout <= 0
      || input.execution_timeout > 150) {
    throw new Error("A valid public HTTP(S) URL is required");
  }
  const socketPath = process.env.SKILL_NETWORK_SOCKET;
  if (!socketPath) throw new Error("Network mediation is unavailable");

  const browser = await chromium.launch({ chromiumSandbox: false, headless: true, args: ["--disable-dev-shm-usage"] });
  let context;
  const typedValues = [];
  try {
    context = await browser.newContext({
      serviceWorkers: "block", acceptDownloads: false, javaScriptEnabled: false,
      viewport: { width: 1280, height: 800 },
    });
    await context.routeWebSocket("**/*", (route) => route.close());
    const openedMainPages = new Set();
    const mainNavigationFinals = new Map();
    let visitedMainUrls = new Set();
    let mainRedirectCount = 0;
    let requestCount = 0;
    let responseBytes = 0;
    let ownerControl = false;
    let ownerGestureSequence = null;
    let ownerActionContext = null;
    let ownerActionOrigin = null;
    let agentActionPrepared = false;
    let ownerRedirectUrl = null;
    let authenticatedContinuation = false;
    const authenticatedLinkContexts = new Map();
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = request.url();
      const isMainNavigation = request.isNavigationRequest()
        && request.frame() === request.frame().page().mainFrame();
      const actionCandidate = input.browser_handoff && ownerControl && isMainNavigation
        && request.method() === "POST" && ownerGestureSequence !== null
        && ["owner", "agent"].includes(ownerActionOrigin);
      if (input.browser_handoff && (!validHttpUrl(url, actionCandidate) || new URL(url).protocol !== "https:")) {
        await route.abort("blockedbyclient");
        return;
      }
      const continuationRead = input.browser_handoff && authenticatedContinuation && !ownerControl;
      if (input.browser_handoff && (ownerControl || continuationRead)) {
        const method = request.method();
        const body = method === "POST" ? request.postDataBuffer() ?? Buffer.alloc(0) : Buffer.alloc(0);
        if (++requestCount > MAX_URLS || !validHttpUrl(url, actionCandidate)
            || !(method === "GET" || method === "POST")
            || continuationRead && method !== "GET"
            || !isMainNavigation && method !== "GET"
            || method === "POST" && (!isMainNavigation || ownerGestureSequence === null
              || !/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers()["content-type"] ?? ""))) {
          await route.abort("blockedbyclient");
          return;
        }
        try {
          const headers = await request.allHeaders();
          const safeHeaders = Object.fromEntries(Object.entries(headers).filter(([name]) =>
            ["accept", "accept-encoding", "accept-language", "content-type", "cookie", "origin", "referer", "user-agent"].includes(name.toLowerCase())));
          const mediated = await hostRequest(socketPath, {
            method, url, headers: safeHeaders, body: body.toString("base64"),
            owner_sequence: continuationRead ? null
              : isMainNavigation && ownerActionOrigin !== "agent" ? ownerGestureSequence : null,
            action_origin: continuationRead ? "continuation" : ownerActionOrigin || "owner",
            main_navigation: isMainNavigation,
            action_context: continuationRead && isMainNavigation
              ? authenticatedLinkContexts.get(url) ?? { kind: "link", summary: "", form_text: "" }
              : ownerActionContext || { kind: "none", summary: "", form_text: "" },
          }, method === "POST" ? 70_000 : 12_000);
          if (isMainNavigation) {
            ownerGestureSequence = null;
            ownerActionContext = null;
            ownerActionOrigin = null;
          }
          responseBytes += mediated.body.length;
          if (responseBytes > MAX_BROWSER_BYTES) throw new Error("Page resource limit");
          const cookies = mediated.headers["set-cookie"];
          if (cookies !== undefined) {
            if (!Array.isArray(cookies) || cookies.length > 16) throw new Error("Cookie response limit");
            for (const cookie of cookies) await installContextCookie(context, url, cookie);
          }
          const responseHeaders = Object.fromEntries(Object.entries(mediated.headers)
            .filter(([name]) => name.toLowerCase() !== "set-cookie"));
          if ([301, 302, 303, 307, 308].includes(mediated.status)) {
            if (!isMainNavigation) throw new Error("Redirected subresources are unavailable during owner control");
            if (continuationRead) {
              const location = Object.entries(responseHeaders).find(([name]) => name.toLowerCase() === "location")?.[1];
              if (typeof location !== "string" || !location) throw new Error("Redirect has no destination");
              const nextUrl = new URL(location, url).href;
              if (!validHttpUrl(nextUrl) || new URL(nextUrl).protocol !== "https:"
                  || visitedMainUrls.has(nextUrl) || ++mainRedirectCount > MAX_REDIRECTS) {
                throw new Error("Unsafe authenticated continuation redirect");
              }
              visitedMainUrls.add(nextUrl);
              mainNavigationFinals.set(url, nextUrl);
              await route.fulfill({
                status: 200,
                headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
                body: "",
              });
              return;
            }
            if (mediated.reviewedAction) {
              await route.fulfill({
                status: 200,
                headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
                body: "<!doctype html><html><head><meta charset=\"utf-8\"><title>Action result needs review</title></head><body><h1>Action result needs review</h1><p>The approved request returned a redirect. Its follow-up request was blocked. Check the site before trying again; the result may be uncertain.</p></body></html>",
              });
              await hostRequest(socketPath, {
                operation: "report_action_status",
                message: "The approved request returned a redirect. Its follow-up was blocked; check the site before retrying.",
              }).catch(() => null);
              return;
            }
            const location = Object.entries(responseHeaders).find(([name]) => name.toLowerCase() === "location")?.[1];
            if (typeof location !== "string" || !location) throw new Error("Redirect has no destination");
            const nextUrl = new URL(location, url).href;
            if (!validHttpUrl(nextUrl) || new URL(nextUrl).protocol !== "https:"
                || visitedMainUrls.has(nextUrl) || ++mainRedirectCount > MAX_REDIRECTS) {
              throw new Error("Unsafe owner-control redirect");
            }
            visitedMainUrls.add(nextUrl);
            ownerRedirectUrl = nextUrl;
          }
          if (isMainNavigation && ownerRedirectUrl) {
            await route.fulfill({
              status: 200,
              headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
              body: "",
            });
          } else {
            await route.fulfill({ status: mediated.status, headers: responseHeaders, body: mediated.body });
          }
          if (isMainNavigation && request.method() === "GET" && mediated.status >= 200 && mediated.status < 300
              && mediated.status !== 204) openedMainPages.add(url);
        } catch {
          await route.abort("blockedbyclient").catch(() => {});
        }
        return;
      }
      if (request.method() !== "GET" || !validHttpUrl(url) || ++requestCount > MAX_URLS) {
        await route.abort("blockedbyclient");
        return;
      }
      try {
        const mediated = await hostRequest(socketPath, { method: "GET", url });
        if ([301, 302, 303, 307, 308].includes(mediated.status)) {
          if (!isMainNavigation) throw new Error("Redirected subresources are unsupported");
          const location = Object.entries(mediated.headers).find(([name]) => name.toLowerCase() === "location")?.[1];
          if (typeof location !== "string" || !location) throw new Error("Redirect has no destination");
          const nextUrl = new URL(location, url).href;
          if (!validHttpUrl(nextUrl) || input.browser_handoff && new URL(nextUrl).protocol !== "https:"
              || visitedMainUrls.has(nextUrl)
              || ++mainRedirectCount > MAX_REDIRECTS) throw new Error("Unsafe redirect");
          visitedMainUrls.add(nextUrl);
          mainNavigationFinals.set(url, nextUrl);
          await route.fulfill({ status: 200, headers: { "content-type": "text/html; charset=utf-8" }, body: "" });
          return;
        }
        responseBytes += mediated.body.length;
        if (responseBytes > MAX_BROWSER_BYTES) throw new Error("Page resource limit");
        await route.fulfill({ status: mediated.status, headers: mediated.headers, body: mediated.body });
        if (isMainNavigation && mediated.status >= 200 && mediated.status < 300) openedMainPages.add(url);
      } catch {
        await route.abort("blockedbyclient").catch(() => {});
      }
    });

    let page;
    let lastSuccessfulPage;
    const openedPages = new Set();
    let openingMainPage = false;
    context.on("page", (candidate) => {
      if (openingMainPage) openedPages.add(candidate);
      else void candidate.close();
    });
    for (const targetUrl of targetUrls) {
      openingMainPage = true;
      page = await context.newPage();
      openingMainPage = false;
      openedPages.add(page);
      page.setDefaultNavigationTimeout(25_000);
      page.setDefaultTimeout(10_000);
      mainNavigationFinals.clear();
      visitedMainUrls = new Set([targetUrl]);
      mainRedirectCount = 0;
      try {
        await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
        for (let redirects = 0; redirects < MAX_REDIRECTS; redirects += 1) {
          const nextUrl = mainNavigationFinals.get(page.url());
          if (!nextUrl) break;
          await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
        }
      } catch { /* A blocked or failed destination is omitted from sources. */ }
      if (validHttpUrl(page.url()) && publicCitation(page.url())
          && openedMainPages.has(page.url())) lastSuccessfulPage = page;
    }
    if (!lastSuccessfulPage) throw new Error("No public page could be opened");
    page = lastSuccessfulPage;

    async function followRelevantLinks(sourcePages = openedPages) {
      // Rank links by overlap between their visible label and the opened page's
      // title/headings. No page clicks, form submissions, or page scripts run.
      const rankedLinkDestinations = new Map();
      for (const sourcePage of sourcePages) {
        if (!openedMainPages.has(sourcePage.url())) continue;
        let contextText = "";
        try {
          contextText = await sourcePage.title();
          const headingCount = Math.min(await sourcePage.locator("h1, h2, h3").count(), 8);
          for (let index = 0; index < headingCount; index += 1) {
            contextText += ` ${await sourcePage.locator("h1, h2, h3").nth(index).innerText()}`;
          }
        } catch { /* A page without readable headings can still provide its title. */ }
        const contextWords = new Set((contextText.toLowerCase().match(/[a-z0-9]{3,}/g) ?? [])
          .filter((word) => !LINK_STOP_WORDS.has(word)));
        let linkCount = 0;
        try { linkCount = Math.min(await sourcePage.locator("a[href]").count(), MAX_LINKS_PER_PAGE); }
        catch { continue; }
        for (let index = 0; index < linkCount; index += 1) {
          try {
            const link = sourcePage.locator("a[href]").nth(index);
            const href = await link.getAttribute("href");
            const label = (await link.innerText()).slice(0, 200).toLowerCase();
            if (typeof href !== "string" || !href.trim()) continue;
            const destination = new URL(href, sourcePage.url()).href;
            if (!validHttpUrl(destination) || !publicCitation(destination)
                || openedMainPages.has(destination)) continue;
            const labelWords = new Set((label.match(/[a-z0-9]{3,}/g) ?? [])
              .filter((word) => !LINK_STOP_WORDS.has(word)));
            const relevance = [...labelWords].filter((word) => contextWords.has(word)).length;
            if (relevance < 1) continue;
            const previous = rankedLinkDestinations.get(destination);
            if (!previous || relevance > previous.relevance) {
              rankedLinkDestinations.set(destination, {
                relevance, context: { kind: "link", summary: label.slice(0, 300), form_text: contextText.slice(0, 2000) },
              });
            }
          } catch { /* Ignore malformed or inaccessible anchors. */ }
        }
      }

      const linkDestinations = [...rankedLinkDestinations.entries()]
        .sort((left, right) => right[1].relevance - left[1].relevance)
        .slice(0, MAX_LINK_DESTINATIONS)
        .map(([destination]) => destination);
      for (const destination of linkDestinations) {
        if (requestCount >= MAX_URLS || responseBytes >= MAX_BROWSER_BYTES) break;
        authenticatedLinkContexts.set(destination, rankedLinkDestinations.get(destination).context);
        openingMainPage = true;
        const linkedPage = await context.newPage();
        openingMainPage = false;
        openedPages.add(linkedPage);
        linkedPage.setDefaultNavigationTimeout(25_000);
        linkedPage.setDefaultTimeout(10_000);
        mainNavigationFinals.clear();
        visitedMainUrls = new Set([destination]);
        mainRedirectCount = 0;
        try {
          await linkedPage.goto(destination, { waitUntil: "domcontentloaded", timeout: 30_000 });
          for (let redirects = 0; redirects < MAX_REDIRECTS; redirects += 1) {
            const nextUrl = mainNavigationFinals.get(linkedPage.url());
            if (!nextUrl) break;
            await linkedPage.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
          }
        } catch { /* A blocked or failed link is omitted from sources. */ }
        if (validHttpUrl(linkedPage.url()) && publicCitation(linkedPage.url())
            && openedMainPages.has(linkedPage.url())) lastSuccessfulPage = linkedPage;
      }
      page = lastSuccessfulPage;
    }

    if (browserSearch || followPublicLinks) await followRelevantLinks();

    if (input.browser_handoff) {
      failureStage = "owner_control";
      const deadline = Date.now() + Math.min(MAX_SESSION_TTL_MS, Math.max(1_000, (input.execution_timeout - 2) * 1_000));
      let returned = false;
      while (Date.now() < deadline && !returned) {
        const response = await hostRequest(socketPath, { operation: "poll_owner_control" });
        let event;
        try { event = JSON.parse(response.body.toString("utf8")); } catch { throw new Error("Invalid owner-control command"); }
        if (event.type === "take_control") {
          ownerControl = true;
          if (hasActionRequest && !agentActionPrepared) {
            agentActionPrepared = true;
            failureStage = "agent_action_preparation";
            try {
              const prepared = await prepareAgentAction(page, socketPath);
              ownerActionContext = prepared.actionContext;
              ownerActionOrigin = "agent";
              ownerGestureSequence = "agent";
              const navigation = page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 1_000 }).catch(() => null);
              await prepared.submitter.click();
              await navigation;
            } catch {
              ownerGestureSequence = null;
              ownerActionContext = null;
              ownerActionOrigin = null;
              await hostRequest(socketPath, {
                operation: "report_action_status",
                message: "The agent could not prepare this form safely. No form data was sent.",
              }).catch(() => null);
            }
            failureStage = "owner_control";
          }
        }
        else if (event.type === "return_control") {
          ownerControl = false;
          ownerGestureSequence = null;
          returned = true;
          break;
        } else if (event.type === "click" && ownerControl) {
          failureStage = "owner_input";
          ownerGestureSequence = event.sequence;
          ownerActionOrigin = "owner";
          ownerActionContext = await readOwnerActionContext(page, { x: event.x, y: event.y })
            .catch(() => ({ kind: "other", summary: "", form_text: "" }));
          const navigation = page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 1_000 }).catch(() => null);
          await page.mouse.click(event.x, event.y);
          await navigation;
        } else if (event.type === "type" && ownerControl) {
          ownerGestureSequence = null;
          ownerActionContext = null;
          ownerActionOrigin = null;
          typedValues.push(event.text);
          await page.keyboard.type(event.text);
          event.text = "";
        } else if (event.type === "key" && ownerControl) {
          ownerGestureSequence = event.sequence;
          ownerActionOrigin = "owner";
          ownerActionContext = event.key === "Enter"
            ? await readOwnerActionContext(page, { key: true }).catch(() => ({ kind: "other", summary: "", form_text: "" })) : null;
          const navigation = page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 1_000 }).catch(() => null);
          await page.keyboard.press(event.key);
          await navigation;
        } else if (event.type !== "idle") throw new Error("Unsupported owner-control command");
        if (ownerRedirectUrl) {
          failureStage = "owner_redirect_manual";
          const target = ownerRedirectUrl;
          ownerRedirectUrl = null;
          if (page.url() !== target) await page.goto(target, { waitUntil: "domcontentloaded", timeout: 25_000 });
        }
        failureStage = "owner_redirect_chain";
        for (let redirects = 0; redirects < MAX_REDIRECTS; redirects += 1) {
          const nextUrl = mainNavigationFinals.get(page.url());
          if (!nextUrl) break;
          await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 25_000 });
        }
        if (ownerControl) {
          failureStage = "owner_frame_capture";
          const frame = await page.screenshot({ type: "jpeg", quality: 65, fullPage: false });
          if (frame.length > MAX_SCREENSHOT_BYTES) throw new Error("Owner-control frame limit");
          const sent = await hostRequest(socketPath, {
            operation: "publish_owner_view", mime: "image/jpeg", data: frame.toString("base64"),
          });
          if (sent.status < 200 || sent.status >= 300) throw new Error("Owner-control frame unavailable");
          failureStage = "owner_control";
        }
      }
      failureStage = "return_control";
      if (!returned) throw new Error("Owner-control session ended without return");
      if (followAuthenticatedLinks) {
        failureStage = "authenticated_continuation";
        authenticatedContinuation = true;
        await followRelevantLinks([page]);
        authenticatedContinuation = false;
      }
      failureStage = "handoff_cleanup";
      const cleaned = await hostRequest(socketPath, { operation: "cleanup_owner_control" });
      if (cleaned.status < 200 || cleaned.status >= 300) throw new Error("Owner-control cleanup was not confirmed");
      failureStage = "result_extraction";
      const citations = [...openedMainPages].map(publicCitation).filter(Boolean).slice(0, 10);
      const pageRecords = [];
      for (const openedPage of openedPages) {
        const sourceUrl = publicCitation(openedPage.url());
        if ((!sourceUrl || !citations.includes(sourceUrl)) && openedPage !== page) continue;
        const title = redactOwnerText((await openedPage.title()).slice(0, 500), typedValues);
        const text = redactOwnerText(
          (await openedPage.locator("body").innerText({ timeout: 5_000 })).slice(0, 3000), typedValues,
        );
        pageRecords.push({ title, sourceUrl: sourceUrl && citations.includes(sourceUrl) ? sourceUrl : null, text });
      }
      const message = truncateUtf8([
        "Untrusted excerpts from the authenticated browser session (owner-entered values redacted):",
        ...pageRecords.map((record, index) =>
          `Page ${index + 1}: ${record.title}\nSource: ${record.sourceUrl ?? "Uncited browser status"}\nExcerpt:\n${record.text}`),
        "Verified source URLs:", ...citations.map((url) => `- ${url}`),
      ].join("\n\n"), MAX_SEARCH_MESSAGE_BYTES);
      typedValues.fill("");
      failureStage = "result_publish";
      const published = await hostRequest(socketPath, {
        operation: "publish_result", message, citations,
      });
      if (published.status < 200 || published.status >= 300) throw new Error("Browser result could not be published");
      process.stdout.write(JSON.stringify({ status: "complete", message, citations }));
      return;
    }

    const currentView = await page.screenshot({ type: "jpeg", quality: 70, fullPage: false });
    if (currentView.length > MAX_SCREENSHOT_BYTES) throw new Error("Browser view image exceeded the size limit");
    await hostRequest(socketPath, {
      operation: "publish_view", mime: "image/jpeg", data: currentView.toString("base64"),
    });

    const citations = [...openedMainPages].map(publicCitation).filter(Boolean).slice(0, 10);
    const multiPageResult = browserSearch || followPublicLinks;
    const pageRecords = [];
    for (const openedPage of openedPages) {
      const sourceUrl = publicCitation(openedPage.url());
      if (!sourceUrl || !citations.includes(sourceUrl)) continue;
      const title = (await openedPage.title()).slice(0, 500);
      const text = (await openedPage.locator("body").innerText({ timeout: 5_000 })).slice(0, multiPageResult ? 3000 : MAX_PAGE_TEXT);
      pageRecords.push({ title, sourceUrl, text });
    }
    if (multiPageResult && pageRecords.length < 1) throw new Error("No public pages opened successfully");
    const message = multiPageResult
      ? ["Untrusted excerpts from opened public pages:",
         ...pageRecords.map((record, index) => `Page ${index + 1}: ${record.title}\nSource: ${record.sourceUrl}\nExcerpt:\n${record.text}`),
         "Verified source URLs:", ...citations.map((url) => `- ${url}`)].join("\n\n")
      : [
          `Title: ${pageRecords[0]?.title ?? ""}`, `Opened URL: ${page.url()}`, "Page text (untrusted):",
          pageRecords[0]?.text ?? "", "Verified source URLs:", ...citations.map((url) => `- ${url}`),
        ].join("\n").slice(0, MAX_PAGE_TEXT + 4000);
    const browserMessage = multiPageResult ? truncateUtf8(message, MAX_SEARCH_MESSAGE_BYTES) : message;
    const published = await hostRequest(
      socketPath, { operation: "publish_result", message: browserMessage, citations }, multiPageResult ? 125_000 : 12_000,
    );
    if (published.status < 200 || published.status >= 300) throw new Error("Browser result could not be published");

    const sessionTtl = Math.min(MAX_SESSION_TTL_MS, Math.max(1_000, (input.execution_timeout - 2) * 1_000));
    const idleDeadline = Date.now() + sessionTtl;
    while (Date.now() < idleDeadline) {
      const response = await hostRequest(socketPath, { operation: "poll_command" });
      let envelope;
      try { envelope = JSON.parse(response.body.toString("utf8")); } catch { throw new Error("Invalid browser command"); }
      if (envelope.command === "capture_screenshot") {
        if (typeof envelope.command_id !== "string") throw new Error("Invalid screenshot command");
        const screenshot = await page.screenshot({ type: "jpeg", quality: 70, fullPage: false });
        if (screenshot.length > MAX_SCREENSHOT_BYTES) throw new Error("Screenshot exceeded the size limit");
        const saved = await hostRequest(socketPath, {
          operation: "screenshot_result", command_id: envelope.command_id,
          mime: "image/jpeg", data: screenshot.toString("base64"),
        });
        if (saved.status < 200 || saved.status >= 300) throw new Error("Screenshot could not be saved");
        process.stdout.write(JSON.stringify({ status: "complete", message: browserMessage, citations }));
        return;
      }
      if (envelope.command !== null) throw new Error("Unsupported browser command");
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
    process.stdout.write(JSON.stringify({ status: "complete", message: browserMessage, citations }));
  } finally {
    typedValues.fill("");
    if (context) await context.close().catch(() => {});
    await browser.close();
  }
}

main().catch(() => {
  process.stderr.write("The browser session could not be maintained safely.\n");
  process.stdout.write(JSON.stringify({ status: "failed", stage: failureStage }));
  process.exitCode = 1;
});
