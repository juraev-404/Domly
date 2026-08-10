(() => {
    const dialog = document.querySelector("[data-logout-dialog]");
    if (!dialog) return;

    const cancelButton = dialog.querySelector("[data-logout-cancel]");
    const confirmButton = dialog.querySelector("[data-logout-confirm]");
    let pendingForm = null;

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-logout-form]");
        if (!form || form.dataset.logoutConfirmed === "true") return;

        event.preventDefault();
        pendingForm = form;
        if (!dialog.open) dialog.showModal();
    });

    cancelButton.addEventListener("click", () => dialog.close());

    confirmButton.addEventListener("click", () => {
        if (!pendingForm) return;
        pendingForm.dataset.logoutConfirmed = "true";
        confirmButton.disabled = true;
        pendingForm.requestSubmit();
    });

    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
    });

    dialog.addEventListener("close", () => {
        pendingForm = null;
        confirmButton.disabled = false;
    });
})();
