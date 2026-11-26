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
