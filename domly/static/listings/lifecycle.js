(() => {
    const dialog = document.querySelector("[data-listing-action-dialog]");
    if (!dialog) return;

    const title = dialog.querySelector("[data-listing-action-title]");
    const description = dialog.querySelector("[data-listing-action-description]");
    const cancelButton = dialog.querySelector("[data-listing-action-cancel]");
    const confirmButton = dialog.querySelector("[data-listing-action-confirm]");
    let pendingForm = null;

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-listing-confirm-form]");
        if (!form || form.dataset.actionConfirmed === "true") return;

        event.preventDefault();
        pendingForm = form;
        title.textContent = form.dataset.confirmTitle;
        description.textContent = form.dataset.confirmDescription;
        confirmButton.textContent = form.dataset.confirmButton;
        if (!dialog.open) dialog.showModal();
    });

    cancelButton.addEventListener("click", () => dialog.close());

    confirmButton.addEventListener("click", () => {
        if (!pendingForm) return;
        pendingForm.dataset.actionConfirmed = "true";
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
