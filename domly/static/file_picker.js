(() => {
    document.querySelectorAll("[data-file-picker]").forEach((picker) => {
        const input = picker.querySelector("[data-file-picker-input]");
        const status = picker.querySelector("[data-file-picker-status]");
        if (!input || !status) return;

        const emptyLabel = status.textContent.trim();
        const selectedTemplate = picker.dataset.selectedTemplate || "{count}";
        input.addEventListener("change", () => {
            const count = input.files?.length || 0;
            status.textContent = count
                ? selectedTemplate.replace("{count}", String(count))
                : emptyLabel;
        });
    });
})();
