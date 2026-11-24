const fs = require("node:fs");

const base = "http://127.0.0.1:20128";
const model = "google/gemma-4-26b-a4b-it:free";
const expectedRoute = `openrouter/${model}`;
const providers = {
  openrouter: "OpenRouter",
  groq: "Groq",
  gemini: "Gemini",
};

async function request(path, { method = "GET", payload, cookie, token } = {}) {
  const headers = { Origin: base };
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  if (cookie) headers.Cookie = cookie;
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(base + path, {
    method, headers, redirect: "error", signal: AbortSignal.timeout(20000),
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  if (!response.ok) {
    await response.body?.cancel();
    const error = new Error(`${method} ${path.split("?")[0]} returned HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return {
    data: await response.json(),
    cookies: response.headers.getSetCookie(),
  };
}

async function usableToken(path, token, tolerateUnavailable = false) {
  if (!token) return false;
  try {
    await request(path, { token });
    return true;
  } catch (error) {
    if (error.status === 401 || error.status === 403) return false;
    if (tolerateUnavailable && (!error.status || error.status >= 500)) return true;
    throw error;
  }
}

async function bootstrap(input) {
  const login = await request("/api/auth/login", {
    method: "POST", payload: { password: input.password },
  });
  const cookie = login.cookies.find((value) => value.startsWith("auth_token="))?.split(";")[0];
  if (!cookie) throw new Error("OmniRoute did not issue a bootstrap session.");

  let inference = input.inference;
  if (!(await usableToken("/v1/models", inference, true))) {
    const created = await request("/api/keys", {
      method: "POST", cookie, payload: { name: "SuperHarry2 inference", scopes: ["inference"] },
    });
    inference = created.data.key;
  }
  let management = input.management;
  if (!(await usableToken("/api/providers", management))) {
    const created = await request("/api/cli/tokens", {
      method: "POST", cookie, payload: { name: "SuperHarry2 model management", scope: "write" },
    });
    management = created.data.token;
  }
  if (!inference || !management || inference === management) {
    throw new Error("OmniRoute did not produce two separate credentials.");
  }
  if (await usableToken("/api/providers", inference)) {
    throw new Error("The inference credential unexpectedly authorizes management.");
  }

  const provisioning = {};
  const { data } = await request("/api/providers", { cookie });
  for (const [provider, name] of Object.entries(providers)) {
    const existing = data.connections.some((item) => item.provider === provider);
    const apiKey = input.providers?.[provider];
    try {
      if (!existing && apiKey) {
        await request("/api/providers", {
          method: "POST", cookie, payload: { provider, name, apiKey },
        });
      }
      provisioning[provider] = existing ? "preserved" : apiKey ? "configured" : "no-key";
    } catch (error) {
      provisioning[provider] = error.status ? `http-${error.status}` : "unavailable";
    }
  }
  if (["configured", "preserved"].includes(provisioning.openrouter)) {
    try {
      const catalog = await request("/api/provider-models?provider=openrouter", { token: management });
      if (!catalog.data.models.some((item) => item.id === model)) {
        await request("/api/provider-models", {
          method: "POST", token: management,
          payload: {
            provider: "openrouter", modelId: model,
            modelName: "Gemma 4 26B A4B Free", isFree: true,
          },
        });
      }
    } catch (error) {
      provisioning.freeModel = error.status ? `http-${error.status}` : "unavailable";
    }
  }

  let route = expectedRoute;
  let verified = false;
  if (["configured", "preserved"].includes(provisioning.openrouter)) {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const { data } = await request("/v1/models", { token: inference });
        const match = data.data.find((item) => item.id === expectedRoute);
        if (match) {
          route = match.id;
          verified = true;
          break;
        }
      } catch {
        // The catalog may be temporarily unavailable after startup.
      }
      if (attempt < 4) await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  return { inference, management, provisioning, route, verified };
}

bootstrap(JSON.parse(fs.readFileSync(0, "utf8"))).then(
  (result) => process.stdout.write(JSON.stringify(result)),
  (error) => {
    process.stderr.write(error.status ? error.message : "OmniRoute bootstrap failed.");
    process.exitCode = 1;
  },
);
