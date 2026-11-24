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
  article.append(label, content);
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

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = composer.querySelector("button");
  if (button.disabled || !input.value.trim()) return;

  const data = new FormData(composer);
  button.disabled = true;
  input.readOnly = true;
  statusMessage.textContent = "Waiting for the model…";
  try {
    const response = await fetch(composer.action, { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || "Your message could not be sent.");
    }
    if (!response.headers.get("Content-Type")?.startsWith("application/x-ndjson")) {
      throw new Error("Sign in again to continue.");
    }
    const location = response.headers.get("Location");
    historyLink.href = location;
    historyLink.hidden = false;
    appendMessage("You", data.get("message"));
    const output = appendMessage("Assistant", "");
    input.value = "";
    statusMessage.textContent = "Receiving reply…";
    await readReply(response.body, output);
    window.location.assign(location);
  } catch (error) {
    statusMessage.textContent = error.message;
    // When the message has been saved, let the user reopen its conversation.
    if (historyLink.hidden) {
      input.readOnly = false;
      button.disabled = false;
    }
  }
});
