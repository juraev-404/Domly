(() => {
    const dialog = document.querySelector("[data-delete-dialog]");
    if (!dialog) return;

    const cancelButton = dialog.querySelector("[data-delete-cancel]");
    const confirmButton = dialog.querySelector("[data-delete-confirm]");
    let pendingForm = null;

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-delete-conversation-form]");
        if (!form || form.dataset.deleteConfirmed === "true") return;

        event.preventDefault();
        pendingForm = form;
        if (!dialog.open) dialog.showModal();
    });

    cancelButton.addEventListener("click", () => dialog.close());

    confirmButton.addEventListener("click", () => {
        if (!pendingForm) return;
        pendingForm.dataset.deleteConfirmed = "true";
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
