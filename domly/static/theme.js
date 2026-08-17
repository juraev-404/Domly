(() => {
    const STORAGE_KEY = "domly-theme";

    const currentTheme = () => (
        document.documentElement.classList.contains("dark") ? "dark" : "light"
    );

    const saveTheme = (theme) => {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (error) {
            // The theme still works for the current page when storage is blocked.
        }
    };

    const updateButtons = () => {
        const isDark = currentTheme() === "dark";
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            const label = isDark
                ? button.dataset.enableLightLabel
                : button.dataset.enableDarkLabel;
            const darkIcon = button.querySelector('[data-theme-icon="dark"]');
            const lightIcon = button.querySelector('[data-theme-icon="light"]');
            button.setAttribute("aria-label", label);
            button.setAttribute("title", label);
            button.setAttribute("aria-pressed", String(isDark));
            darkIcon.toggleAttribute("hidden", isDark);
            lightIcon.toggleAttribute("hidden", !isDark);
        });
    };

    const applyTheme = (theme, {persist = true} = {}) => {
        const normalizedTheme = theme === "dark" ? "dark" : "light";
        const isDark = normalizedTheme === "dark";
        document.documentElement.classList.toggle("dark", isDark);
        document.documentElement.dataset.theme = normalizedTheme;
        document.documentElement.style.colorScheme = normalizedTheme;
        if (persist) saveTheme(normalizedTheme);
        updateButtons();
    };

    document.addEventListener("DOMContentLoaded", () => {
        updateButtons();
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                applyTheme(currentTheme() === "dark" ? "light" : "dark");
            });
        });
    });

    window.addEventListener("storage", (event) => {
        if (event.key === STORAGE_KEY && ["light", "dark"].includes(event.newValue)) {
            applyTheme(event.newValue, {persist: false});
        }
    });
})();
