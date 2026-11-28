(() => {
  const fileInput = document.querySelector("#avatar");
  const preview = document.querySelector("#selected-avatar-preview");
  const saved = document.querySelector("#saved-avatar");
  const initial = document.querySelector("#avatar-initial");
  const cancelButton = document.querySelector("#cancel-avatar-selection");
  let previewUrl = null;

  if (fileInput && preview && cancelButton) {
    const restoreSavedImage = () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      preview.removeAttribute("src");
      preview.hidden = true;
      if (saved) saved.hidden = !saved.getAttribute("src");
      if (initial) initial.hidden = Boolean(saved && saved.getAttribute("src"));
      fileInput.value = "";
      cancelButton.hidden = true;
    };

    fileInput.addEventListener("change", () => {
      const [file] = fileInput.files || [];
      if (!file) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
      preview.hidden = false;
      if (saved) saved.hidden = true;
      if (initial) initial.hidden = true;
      cancelButton.hidden = false;
    });
    fileInput.addEventListener("cancel", restoreSavedImage);
    cancelButton.addEventListener("click", restoreSavedImage);
  }

  const showPasswords = document.querySelector("#show-passwords");
  if (showPasswords) {
    showPasswords.addEventListener("change", () => {
      document.querySelectorAll(".password-form input[type='password'], .password-form input[type='text']")
        .forEach((input) => { input.type = showPasswords.checked ? "text" : "password"; });
    });
  }
})();
