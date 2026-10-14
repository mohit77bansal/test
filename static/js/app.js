// Small progressive enhancements; the portal works without JavaScript except for HTMX polling.
document.addEventListener("DOMContentLoaded", () => {
  // Confirm destructive actions.
  document.body.addEventListener("submit", (e) => {
    const msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
  // Client-side size/type check before upload (server re-validates).
  document.querySelectorAll("input[type=file][data-max-bytes]").forEach((input) => {
    input.addEventListener("change", () => {
      const max = parseInt(input.dataset.maxBytes, 10);
      const f = input.files[0];
      input.setCustomValidity(f && f.size > max ? "File is larger than 10 MB." : "");
    });
  });
});
