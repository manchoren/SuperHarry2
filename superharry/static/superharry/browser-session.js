(() => {
  const panel = document.querySelector("[data-browser-session]");
  if (!panel) return;
  const view = panel.querySelector("[data-browser-view]");
  const toggle = panel.querySelector("[data-browser-open]");
  const toggleLabel = panel.querySelector("[data-browser-toggle-label]");
  const hideIcon = panel.querySelector("[data-browser-hide-icon]");
  const showIcon = panel.querySelector("[data-browser-show-icon]");
  const status = panel.querySelector("[data-screenshot-status]");
  const screenshotForm = panel.querySelector("[data-screenshot-form]");
  const statusUrl = panel.dataset.statusUrl;
  const returnButton = document.querySelector("[data-return-chat]");

  returnButton?.addEventListener("click", () => {
    const destination = returnButton.dataset.chatUrl;
    if (destination) window.location.assign(destination);
  });

  toggle.addEventListener("click", () => {
    const hidden = view.hidden;
    view.hidden = !hidden;
    toggleLabel.textContent = hidden ? "Hide browser" : "Open browser";
    hideIcon.classList.toggle("is-hidden", !hidden);
    showIcon.classList.toggle("is-hidden", hidden);
    toggle.setAttribute("aria-expanded", String(hidden));
  });

  screenshotForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = screenshotForm.querySelector("button");
    button.disabled = true;
    status.textContent = "Screenshot request queued...";
    try {
      const response = await fetch(screenshotForm.action, {
        method: "POST", body: new FormData(screenshotForm),
        credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok && response.status !== 202) throw new Error("Request was not accepted");
      status.textContent = "Capturing screenshot. It will appear in this task's artifacts.";
      refreshUntilArtifactsImport();
    } catch {
      status.textContent = "Screenshot could not be requested. Refresh and try again.";
      button.disabled = false;
    }
  });

  const refreshUntilArtifactsImport = async () => {
    try {
      const response = await fetch(statusUrl, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) {
        const state = await response.json();
        if (!state.pending) {
          window.location.reload();
          return;
        }
      }
    } catch { /* retry while the owner-scoped worker session is active */ }
    window.setTimeout(refreshUntilArtifactsImport, 1200);
  };

  const ownerFrame = panel.querySelector("[data-owner-frame]");
  if (ownerFrame) {
    const csrf = panel.querySelector("[data-owner-csrf] input[name=csrfmiddlewaretoken]")?.value;
    const inputUrl = panel.dataset.inputUrl;
    const actionStatusUrl = panel.dataset.actionStatusUrl;
    const actionDecisionUrl = panel.dataset.actionDecisionUrl;
    const actionReview = panel.querySelector("[data-action-review]");
    let lastActionNoticeId = null;
    const sendOwnerEvent = async (payload) => {
      const response = await fetch(inputUrl, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("Owner control unavailable");
    };
    const refreshActionReview = async () => {
      try {
        const response = await fetch(actionStatusUrl, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) throw new Error("Action review unavailable");
        const state = await response.json();
        if (state.notice && state.notice.id !== lastActionNoticeId) {
          lastActionNoticeId = state.notice.id;
          status.textContent = state.notice.message;
        }
        if (state.pending && state.proposal) {
          const proposal = state.proposal;
          actionReview.querySelector("[data-action-summary]").textContent = `Website action: ${proposal.summary}`;
          actionReview.querySelector("[data-action-destination]").textContent = `${proposal.method} ${proposal.url}`;
          actionReview.querySelector("[data-action-form-text]").textContent = proposal.form_text || "No additional labels were available.";
          const fields = actionReview.querySelector("[data-action-fields]");
          fields.replaceChildren();
          for (const field of proposal.fields) {
            const item = document.createElement("li");
            item.textContent = `${field.name}: ${field.value}`;
            fields.append(item);
          }
          actionReview.dataset.proposalId = proposal.id;
          actionReview.dataset.proposalDigest = proposal.digest;
          actionReview.hidden = false;
        } else {
          actionReview.hidden = true;
          delete actionReview.dataset.proposalId;
          delete actionReview.dataset.proposalDigest;
        }
      } catch {
        actionReview.hidden = true;
      }
      window.setTimeout(refreshActionReview, 800);
    };
    actionReview?.querySelectorAll("[data-action-decision]").forEach((button) => {
      button.addEventListener("click", async () => {
        const proposalId = actionReview.dataset.proposalId;
        const digest = actionReview.dataset.proposalDigest;
        const decision = button.dataset.actionDecision;
        const csrf = panel.querySelector("[data-owner-csrf] input[name=csrfmiddlewaretoken]")?.value;
        if (!proposalId || !digest || !csrf) return;
        actionReview.querySelectorAll("button").forEach((item) => { item.disabled = true; });
        try {
          const response = await fetch(actionDecisionUrl, {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf },
            body: new URLSearchParams({ proposal_id: proposalId, digest, decision }),
          });
          if (!response.ok) throw new Error("Decision no longer current");
          actionReview.hidden = true;
          status.textContent = decision === "approve"
            ? "Approval recorded for this exact request. Waiting for the website response."
            : "This request was rejected and will not be sent.";
        } catch {
          status.textContent = "This action is no longer pending. Refresh the task to continue.";
        } finally {
          actionReview.querySelectorAll("button").forEach((item) => { item.disabled = false; });
        }
      });
    });
    if (actionReview) refreshActionReview();
    const refreshFrame = () => {
      if (document.visibilityState === "visible") {
        const separator = ownerFrame.src.includes("?") ? "&" : "?";
        ownerFrame.src = `${ownerFrame.src.split("?")[0]}${separator}frame=${Date.now()}`;
      }
      window.setTimeout(refreshFrame, 500);
    };
    refreshFrame();
    ownerFrame.addEventListener("click", async (event) => {
      const rect = ownerFrame.getBoundingClientRect();
      const scale = Math.min(rect.width / 1280, rect.height / 800);
      const width = 1280 * scale;
      const height = 800 * scale;
      const x = Math.floor((event.clientX - rect.left - (rect.width - width) / 2) / scale);
      const y = Math.floor((event.clientY - rect.top - (rect.height - height) / 2) / scale);
      if (x < 0 || x >= 1280 || y < 0 || y >= 800) return;
      try { await sendOwnerEvent({ type: "click", x, y }); }
      catch { status.textContent = "Browser control ended. Refresh this page."; }
    });
    const typeButton = panel.querySelector("[data-owner-type]");
    const textInput = panel.querySelector("[data-owner-text]");
    typeButton?.addEventListener("click", async () => {
      const value = textInput.value;
      if (!value) return;
      textInput.value = "";
      typeButton.disabled = true;
      try { await sendOwnerEvent({ type: "type", text: value }); }
      catch { status.textContent = "Browser control ended. Refresh this page."; }
      finally { typeButton.disabled = false; }
    });
    panel.querySelectorAll("[data-owner-key]").forEach((button) => {
      button.addEventListener("click", async () => {
        try { await sendOwnerEvent({ type: "key", key: button.dataset.ownerKey }); }
        catch { status.textContent = "Browser control ended. Refresh this page."; }
      });
    });
  }
})();
