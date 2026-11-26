const composer = document.querySelector("#composer");
const input = document.querySelector("#message");
const statusMessage = document.querySelector("#chat-status");
const historyLink = document.querySelector("#chat-history");
const messages = document.querySelector("#messages");

function appendMessage(role, text) {
  const article = document.createElement("article");
  const label = document.createElement("strong");
  const content = document.createElement("p");
  label.textContent = role;
  content.textContent = text;
  article.append(label);
  if (role === "You") {
    const avatar = document.querySelector(".sidebar-footer .user-avatar");
    if (avatar) article.append(avatar.cloneNode(true));
  }
  article.append(content);
  messages.append(article);
  return content;
}

async function readReply(body, output) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("The model reply was interrupted.");
      const lines = (pending + value).split("\n");
      pending = lines.pop();
      for (const line of lines) {
        const event = JSON.parse(line);
        if (event.error) throw new Error(event.error);
        if (event.done) return;
        output.append(document.createTextNode(event.text));
      }
    }
  } finally {
    await reader.cancel();
  }
}

const chatForms = [composer, ...document.querySelectorAll(".chat-retry")];
const chatButtons = chatForms.map((form) => form.querySelector("button"));

async function submitChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const sendingMessage = form === composer;
  if (chatButtons[0].disabled || (sendingMessage && !input.value.trim())) return;

  const data = new FormData(form);
  if (sendingMessage && window.chatFilePond) {
    data.delete("attachment");
    window.chatFilePond.getFiles().forEach((item) => data.append("attachment", item.file, item.filename));
  }
  chatButtons.forEach((button) => { button.disabled = true; });
  input.readOnly = true;
  statusMessage.textContent = "Waiting for the model…";
  try {
    const response = await fetch(form.action, { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || "Your message could not be sent.");
    }
    const location = response.headers.get("Location");
    if (response.status === 204 && location) {
      window.location.assign(location);
      return;
    }
    if (!response.headers.get("Content-Type")?.startsWith("application/x-ndjson")) {
      throw new Error("Sign in again to continue.");
    }
    historyLink.href = location;
    historyLink.hidden = false;
    if (sendingMessage) {
      appendMessage("You", data.get("message"));
      input.value = "";
    }
    const output = appendMessage("Assistant", "");
    statusMessage.textContent = "Receiving reply…";
    await readReply(response.body, output);
    window.location.assign(location);
  } catch (error) {
    statusMessage.textContent = error.message;
    // When the message has been saved, let the user reopen its conversation.
    if (historyLink.hidden) {
      input.readOnly = false;
      chatButtons.forEach((button) => { button.disabled = false; });
    }
  }
}

chatForms.forEach((form) => form.addEventListener("submit", submitChat));

const conversationNav = document.querySelector(".conversation-nav");
if (conversationNav) {
  let busy = false;
  let visibleOpenRecorded = false;
  const relative = (timestamp) => {
    if (!timestamp) return "Seen";
    const seconds = Math.max(0, (Date.now() - Date.parse(timestamp)) / 1000);
    if (seconds < 60) return "Seen just now";
    const units = [[31536000, "y"], [604800, "wk"], [86400, "d"], [3600, "h"], [60, "min"]];
    const [size, label] = units.find(([length]) => seconds >= length);
    return `Seen ${Math.floor(seconds / size)} ${label} ago`;
  };
  async function updateConversationStates() {
    if (busy) return;
    busy = true;
    try {
      let response = await fetch(conversationNav.dataset.statusUrl, {cache: "no-store"});
      if (!response.ok) return;
      let data = await response.json();
      const current = data.conversations.find((item) => String(item.id) === conversationNav.dataset.activeId);
      if (document.visibilityState === "visible" && current && (!visibleOpenRecorded || current.state === "unread")) {
        const token = document.querySelector('#composer input[name="csrfmiddlewaretoken"]')?.value;
        if (token) {
          const body = new URLSearchParams();
          if (!visibleOpenRecorded) body.set("opened", "1");
          const marked = await fetch(conversationNav.dataset.seenUrl, {method: "POST", headers: {"X-CSRFToken": token}, body});
          if (marked.ok) {
            visibleOpenRecorded = true;
            response = await fetch(conversationNav.dataset.statusUrl, {cache: "no-store"});
            if (response.ok) data = await response.json();
          }
        }
      }
      data.conversations.forEach((item) => {
        const label = conversationNav.querySelector(`[data-conversation-id="${item.id}"]`);
        if (label) label.textContent = item.state === "thinking" ? "◌ Thinking…" : item.state === "unread" ? "● New response" : `✓ ${relative(item.last_opened)}`;
      });
    } catch { /* Retry on the next poll. */ }
    finally { busy = false; }
  }
  updateConversationStates();
  window.setInterval(updateConversationStates, 5000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") updateConversationStates(); });
}

document.querySelectorAll(".skill-artifacts").forEach((container) => {
  async function refresh() {
    try {
      const response = await fetch(container.dataset.statusUrl, {cache: "no-store"});
      if (!response.ok) return;
      const value = await response.json();
      container.replaceChildren();
      value.artifacts.forEach((artifact) => {
        const row = document.createElement("p");
        const link = document.createElement("a");
        link.href = artifact.url;
        link.textContent = `Download ${artifact.name}`;
        row.append(link);
        const preview = document.createElement("button");
        preview.type = "button";
        preview.textContent = "Preview";
        preview.addEventListener("click", async () => {
          if (!window.previewChatFile) await import("./file-preview.js");
          window.previewChatFile(artifact.name, artifact.url);
        });
        row.append(document.createTextNode(" "), preview);
        if (artifact.partial) row.append(document.createTextNode(" (partial result)"));
        container.append(row);
      });
      if (value.pending) window.setTimeout(refresh, 3000);
      else if (value.status !== "complete" && value.artifacts.length) container.append(document.createTextNode("Execution failed or timed out; these files are partial."));
    } catch {
      window.setTimeout(refresh, 5000);
    }
  }
  refresh();
});
