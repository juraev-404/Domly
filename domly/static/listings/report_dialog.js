(() => {
    const dialog = document.querySelector("[data-report-dialog]");
    const opener = document.querySelector("[data-report-open]");
    if (!dialog || !opener) return;

    opener.addEventListener("click", () => dialog.showModal());
    dialog.querySelectorAll("[data-report-close]").forEach((button) => {
        button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
    });
})();
