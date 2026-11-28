const fs = require("node:fs");
const crypto = require("node:crypto");

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
  let localRoute = "";
  if (input.ollama?.available && input.ollama?.model) {
    const modelName = input.ollama.model;
    const prefix = "superharry-local";
    const baseUrl = "http://host.docker.internal:11434/v1";
    const nodesResponse = await request("/api/provider-nodes", { token: management });
    const nodes = Array.isArray(nodesResponse.data.nodes) ? nodesResponse.data.nodes : [];
    let node = nodes.find((item) => item.name === "SuperHarry2 Ollama");
    if (node && (node.prefix !== prefix || node.baseUrl !== baseUrl || node.apiType !== "chat")) {
      throw new Error("The existing SuperHarry2 Ollama node has a different configuration.");
    }
    if (!node) {
      const created = await request("/api/provider-nodes", {
        method: "POST", token: management,
        payload: {name: "SuperHarry2 Ollama", prefix, type: "openai-compatible", apiType: "chat", baseUrl},
      });
      node = created.data.node;
    }
    if (!node?.id) throw new Error("OmniRoute did not return the local provider node.");

    const providersResponse = await request("/api/providers", { token: management });
    const connections = Array.isArray(providersResponse.data.connections) ? providersResponse.data.connections : [];
    let connection = connections.find((item) => item.provider === node.id);
    if (!connection) {
      // OmniRoute 3.8.53 requires a credential record for keyless compatible nodes.
      const created = await request("/api/providers", {
        method: "POST", cookie, payload: {
          provider: node.id, name: "SuperHarry2 local Ollama",
          apiKey: `local-${crypto.randomUUID()}`,
        },
      });
      connection = created.data.connection;
    }
    if (!connection?.id) throw new Error("OmniRoute did not return the local provider connection.");

    const customModels = await request(`/api/provider-models?provider=${encodeURIComponent(node.id)}`, { token: management });
    const knownModels = Array.isArray(customModels.data.models) ? customModels.data.models : [];
    if (!knownModels.some((item) => item.id === modelName)) {
      await request("/api/provider-models", {
        method: "POST", token: management,
        payload: {provider: node.id, modelId: modelName, modelName, isFree: false},
      });
    }
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        const models = await request("/v1/models", { token: inference });
        const id = `${prefix}/${modelName}`;
        if (models.data.data.some((item) => item.id === id)) {
          localRoute = id;
          break;
        }
      } catch {
        // The connection test and model catalog refresh run asynchronously.
      }
      if (attempt < 9) await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    provisioning.ollama = localRoute ? "ready" : "route-unavailable";
  } else {
    provisioning.ollama = input.ollama?.model ? "unavailable" : "not-configured";
  }
  const autoTargets = [
    ...(verified ? [route] : []),
    ...(localRoute ? [localRoute] : []),
  ];
  let autoRoute = "";
  if (autoTargets.length) {
    const name = "superharry-auto";
    const combosResponse = await request("/api/combos", { token: management });
    const combos = Array.isArray(combosResponse.data.combos) ? combosResponse.data.combos : [];
    const existing = combos.find((item) => item.name === name);
    const expected = { name, displayName: "SuperHarry2 Auto", models: autoTargets, strategy: "priority",
      config: { maxRetries: 0, zeroLatencyOptimizationsEnabled: false, reasoningTransportFallback: "drop" } };
    if (existing) {
      const actualTargets = Array.isArray(existing.models) ? existing.models.map((item) => typeof item === "string" ? item : item.model) : [];
      if (JSON.stringify(actualTargets) !== JSON.stringify(autoTargets) || existing.strategy !== "priority"
          || existing.config?.zeroLatencyOptimizationsEnabled !== false || existing.config?.maxRetries !== 0
          || existing.config?.reasoningTransportFallback !== "drop" || existing.isActive === false) {
        await request(`/api/combos/${encodeURIComponent(existing.id)}`, {method: "PUT", token: management, payload: expected});
      }
    } else {
      await request("/api/combos", { method: "POST", token: management, payload: expected });
    }
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        const models = await request("/v1/models", { token: inference });
        if (models.data.data.some((item) => item.id === name)) { autoRoute = name; break; }
      } catch {
        // The combo catalog may refresh asynchronously after a write.
      }
      if (attempt < 9) await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  return { inference, management, provisioning, route, verified, localRoute, autoRoute, autoTargets };
}

bootstrap(JSON.parse(fs.readFileSync(0, "utf8"))).then(
  (result) => process.stdout.write(JSON.stringify(result)),
  (error) => {
    process.stderr.write(error.status ? error.message : "OmniRoute bootstrap failed.");
    process.exitCode = 1;
  },
);
