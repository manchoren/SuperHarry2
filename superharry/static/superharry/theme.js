(() => {
  const key = "superharry-theme";
  let preference = "system";
  try {
    const saved = localStorage.getItem(key);
    if (["system", "light", "dark"].includes(saved)) preference = saved;
  } catch (_) { /* The system preference remains available without storage. */ }
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    document.documentElement.dataset.theme = preference === "system" ? (media.matches ? "dark" : "light") : preference;
    document.querySelectorAll("[data-theme-select]").forEach((select) => { select.value = preference; });
  };
  apply();
  media.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll("[data-theme-select]").forEach((select) => {
      select.addEventListener("change", () => {
        preference = select.value;
        try { localStorage.setItem(key, preference); } catch (_) { /* Keep the choice for this page. */ }
        apply();
      });
    });
    const toggle = document.querySelector(".menu-toggle");
    if (toggle) toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      document.body.classList.toggle("menu-open", !expanded);
    });
  });
})();
