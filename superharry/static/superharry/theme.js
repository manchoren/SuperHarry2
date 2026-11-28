(() => {
  const key = "superharry-theme";
  let preference = "system";
  try {
    const saved = localStorage.getItem(key);
    if (["system", "light", "dark"].includes(saved)) preference = saved;
  } catch (_) { /* The system preference remains available without storage. */ }
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const choices = ["light", "system", "dark"];
  const apply = () => {
    document.documentElement.dataset.theme = preference === "system" ? (media.matches ? "dark" : "light") : preference;
    document.querySelectorAll("[data-theme-select]").forEach((slider) => {
      slider.value = String(choices.indexOf(preference));
      slider.setAttribute("aria-valuetext", preference[0].toUpperCase() + preference.slice(1));
    });
    document.querySelectorAll("[data-theme-value]").forEach((label) => { label.textContent = preference[0].toUpperCase() + preference.slice(1); });
  };
  apply();
  media.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll("[data-theme-select]").forEach((slider) => {
      slider.addEventListener("input", () => {
        preference = choices[Number(slider.value)] || "system";
        try { localStorage.setItem(key, preference); } catch (_) { /* Keep the choice for this page. */ }
        apply();
      });
    });
    const toggle = document.querySelector(".menu-toggle");
    if (toggle) {
      const mobile = window.matchMedia("(max-width: 50rem)");
      const sidebar = document.querySelector("#site-sidebar");
      const setOpen = (open) => {
        const focusInsidePanel = sidebar?.contains(document.activeElement);
        document.body.classList.toggle("sidebar-closed", !open);
        toggle.setAttribute("aria-expanded", String(open));
        if (!open && focusInsidePanel) toggle.focus();
      };
      setOpen(!mobile.matches);
      toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));
      document.querySelector(".sidebar-close")?.addEventListener("click", () => setOpen(false));
      mobile.addEventListener("change", () => setOpen(!mobile.matches));
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
          setOpen(false);
          toggle.focus();
        }
      });
    }
  });
})();
