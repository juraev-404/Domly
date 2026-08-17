(() => {
    const root = document.querySelector("[data-chat-root]");
    if (!root) return;

    const list = root.querySelector("[data-message-list]");
    const form = root.querySelector("[data-message-form]");
    const textarea = form.querySelector("textarea[name='body']");
    const submitButton = form.querySelector("button[type='submit']");
    const clientIdInput = form.querySelector("[data-client-id]");
    const imageInput = form.querySelector("input[name='images']");
    const imagePreview = form.querySelector("[data-image-preview]");
    const errorNode = form.querySelector("[data-chat-error]");
    const statusNode = form.querySelector("[data-chat-status]");
    const eventsUrl = root.dataset.eventsUrl;
    const text = {
        openPhoto: root.dataset.i18nOpenPhoto,
        connectionRetry: root.dataset.i18nConnectionRetry,
        messageRequired: root.dataset.i18nMessageRequired,
        sending: root.dataset.i18nSending,
        sendFailed: root.dataset.i18nSendFailed,
        connectionFailed: root.dataset.i18nConnectionFailed,
    };
    const lightbox = document.querySelector("[data-chat-lightbox]");
    const lightboxImage = lightbox.querySelector("[data-chat-lightbox-image]");
    const lightboxCounter = lightbox.querySelector("[data-chat-lightbox-counter]");
    const lightboxClose = lightbox.querySelector("[data-chat-lightbox-close]");
    const lightboxPrev = lightbox.querySelector("[data-chat-lightbox-prev]");
    const lightboxNext = lightbox.querySelector("[data-chat-lightbox-next]");
    let cursor = Math.max(
        0,
        ...Array.from(list.querySelectorAll("[data-message-id]"), (node) =>
            Number(node.dataset.messageId)
        )
    );
    let polling = false;
    let previewUrls = [];
    let lightboxItems = [];
    let lightboxIndex = 0;
    let lightboxOpener = null;

    const scrollToBottom = () => {
        list.scrollTop = list.scrollHeight;
    };

    const showLightboxImage = (index) => {
        lightboxIndex = (index + lightboxItems.length) % lightboxItems.length;
        const item = lightboxItems[lightboxIndex];
        lightboxImage.src = item.dataset.src;
        lightboxImage.alt = item.dataset.alt;
        lightboxCounter.textContent = `${lightboxIndex + 1} / ${lightboxItems.length}`;
    };

    const openLightbox = (opener) => {
        lightboxOpener = opener;
        lightboxItems = Array.from(
            opener.closest("[data-message-id]").querySelectorAll("[data-chat-image]")
        );
        const hasMultipleImages = lightboxItems.length > 1;
        lightboxPrev.classList.toggle("hidden", !hasMultipleImages);
        lightboxPrev.classList.toggle("flex", hasMultipleImages);
        lightboxNext.classList.toggle("hidden", !hasMultipleImages);
        lightboxNext.classList.toggle("flex", hasMultipleImages);
        showLightboxImage(lightboxItems.indexOf(opener));
        lightbox.classList.remove("hidden");
        lightbox.classList.add("flex");
        document.body.classList.add("overflow-hidden");
        lightboxClose.focus();
    };

    const closeLightbox = () => {
        lightbox.classList.add("hidden");
        lightbox.classList.remove("flex");
        document.body.classList.remove("overflow-hidden");
        lightboxImage.src = "";
        lightboxOpener?.focus();
    };

    const appendMessage = (message) => {
        if (list.querySelector(`[data-message-id="${message.id}"]`)) return;
        list.querySelector("[data-empty-chat]")?.remove();

        const row = document.createElement("div");
        row.dataset.messageId = String(message.id);
        row.className = `flex ${message.is_mine ? "justify-end" : "justify-start"}`;

        const bubble = document.createElement("div");
        bubble.className = message.is_mine
            ? "max-w-[82%] rounded-2xl rounded-br-md bg-black px-3.5 py-2.5 text-sm text-white shadow-sm"
            : "max-w-[82%] rounded-2xl rounded-bl-md bg-white px-3.5 py-2.5 text-sm text-gray-900 shadow-sm";

        const body = document.createElement("p");
        body.className = "whitespace-pre-wrap break-words";
        body.textContent = message.body;

        if (message.attachments.length) {
            const gallery = document.createElement("div");
            gallery.className = `mb-2 grid max-w-72 gap-1 overflow-hidden rounded-xl ${message.attachments.length === 1 ? "grid-cols-1" : "grid-cols-2"}`;
            message.attachments.forEach((attachment, index) => {
                const imageButton = document.createElement("button");
                imageButton.type = "button";
                imageButton.dataset.chatImage = "";
                imageButton.dataset.src = attachment.url;
                imageButton.dataset.alt = attachment.name;
                imageButton.setAttribute(
                    "aria-label",
                    text.openPhoto
                        .replace("{current}", String(index + 1))
                        .replace("{total}", String(message.attachments.length))
                );
                imageButton.className = "block w-full overflow-hidden rounded-lg bg-gray-200 text-left";
                const image = document.createElement("img");
                image.src = attachment.url;
                image.alt = attachment.name;
                image.className = "max-h-64 w-full object-cover";
                imageButton.append(image);
                gallery.append(imageButton);
            });
            bubble.append(gallery);
        }

        const time = document.createElement("time");
        time.className = "mt-1 block text-right text-[10px] text-gray-400";
        time.textContent = message.created_label;

        if (message.body) bubble.append(body);
        bubble.append(time);
        row.append(bubble);
        list.append(row);
        cursor = Math.max(cursor, Number(message.id));
    };

    const showError = (text) => {
        errorNode.textContent = text;
        errorNode.classList.toggle("hidden", !text);
    };

    const clearPreview = () => {
        previewUrls.forEach((url) => URL.revokeObjectURL(url));
        previewUrls = [];
        imagePreview.replaceChildren();
        imagePreview.classList.add("hidden");
        imagePreview.classList.remove("flex");
    };

    const renderPreview = () => {
        clearPreview();
        const files = Array.from(imageInput.files || []);
        if (!files.length) return;
        imagePreview.classList.remove("hidden");
        imagePreview.classList.add("flex");
        files.forEach((file) => {
            const url = URL.createObjectURL(file);
            previewUrls.push(url);
            const image = document.createElement("img");
            image.src = url;
            image.alt = file.name;
            image.className = "h-16 w-16 shrink-0 rounded-xl border border-gray-200 object-cover";
            imagePreview.append(image);
        });
    };

    const poll = async () => {
        if (polling || document.hidden) return;
        polling = true;
        try {
            const response = await fetch(`${eventsUrl}?after=${cursor}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) return;
            const data = await response.json();
            const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;
            data.messages.forEach(appendMessage);
            if (nearBottom && data.messages.length) scrollToBottom();
            statusNode.textContent = "";
        } catch (_) {
            statusNode.textContent = text.connectionRetry;
        } finally {
            polling = false;
        }
    };

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        showError("");
        if (!textarea.value.trim() && !imageInput.files.length) {
            showError(text.messageRequired);
            return;
        }

        submitButton.disabled = true;
        statusNode.textContent = text.sending;
        if (globalThis.crypto?.randomUUID) clientIdInput.value = crypto.randomUUID();

        try {
            const response = await fetch(form.action || window.location.href, {
                method: "POST",
                body: new FormData(form),
                headers: {
                    Accept: "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });
            const data = await response.json();
            if (!response.ok) {
                const firstError = data.errors?.body?.[0]?.message;
                showError(firstError || text.sendFailed);
                return;
            }
            appendMessage(data.message);
            textarea.value = "";
            imageInput.value = "";
            clearPreview();
            clientIdInput.value = "";
            scrollToBottom();
        } catch (_) {
            showError(text.connectionFailed);
        } finally {
            submitButton.disabled = false;
            statusNode.textContent = "";
            textarea.focus();
        }
    });

    textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    imageInput.addEventListener("change", renderPreview);

    list.addEventListener("click", (event) => {
        const imageButton = event.target.closest("[data-chat-image]");
        if (imageButton) openLightbox(imageButton);
    });
    lightboxClose.addEventListener("click", closeLightbox);
    lightboxPrev.addEventListener("click", () => showLightboxImage(lightboxIndex - 1));
    lightboxNext.addEventListener("click", () => showLightboxImage(lightboxIndex + 1));
    lightbox.addEventListener("click", (event) => {
        if (event.target === lightbox) closeLightbox();
    });
    document.addEventListener("keydown", (event) => {
        if (lightbox.classList.contains("hidden")) return;
        if (event.key === "Escape") closeLightbox();
        if (event.key === "ArrowLeft") showLightboxImage(lightboxIndex - 1);
        if (event.key === "ArrowRight") showLightboxImage(lightboxIndex + 1);
    });

    scrollToBottom();
    window.setInterval(poll, 3000);
    document.addEventListener("visibilitychange", poll);
})();
