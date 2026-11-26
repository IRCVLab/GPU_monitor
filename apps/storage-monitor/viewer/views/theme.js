function readThemeModeCookie() {
  const parts = (document.cookie || "").split("; ");
  const found = parts.find((part) => part.startsWith("themeMode="));
  return found ? found.split("=")[1] : "";
}

function preferredThemeMode() {
  const saved = readThemeModeCookie();
  if (saved === "light" || saved === "dark") return saved;
  if (typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

function updateThemeModeButton(mode) {
  const btn = document.getElementById("themeModeButton");
  if (!btn) return;
  const isDark = mode === "dark";
  btn.setAttribute("aria-pressed", isDark ? "true" : "false");
  btn.setAttribute("title", isDark ? "Switch to light mode" : "Switch to dark mode");
}

function applyStoredThemeMode(mode) {
  const next = mode === "light" || mode === "dark" ? mode : preferredThemeMode();
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(next);
  root.dataset.material = "liquid";
  updateThemeModeButton(next);
  return next;
}

function farthestThemeRevealRadius(x, y) {
  const viewportWidth = Math.max(0, Number(window.innerWidth) || 0);
  const viewportHeight = Math.max(0, Number(window.innerHeight) || 0);
  return Math.hypot(
    Math.max(x, viewportWidth - x),
    Math.max(y, viewportHeight - y)
  ) + 24;
}

function themeRectCenter(rect) {
  if (!rect) return null;
  const left = Number(rect.left);
  const top = Number(rect.top);
  const width = Number(rect.width);
  const height = Number(rect.height);
  if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
  return { x: left + width / 2, y: top + height / 2 };
}

function currentThemeIconCenter(button, root) {
  if (!button || typeof button.querySelector !== "function") return null;
  const iconSelector = root.classList.contains("dark") ? ".theme-icon-sun" : ".theme-icon-moon";
  const icon = button.querySelector(iconSelector);
  if (!icon || typeof icon.getBoundingClientRect !== "function") return null;
  return themeRectCenter(icon.getBoundingClientRect());
}

function themeButtonCenter(button) {
  if (!button || typeof button.getBoundingClientRect !== "function") return null;
  return themeRectCenter(button.getBoundingClientRect());
}

function safeThemeRevealCenter() {
  const viewportWidth = Math.max(0, Number(window.innerWidth) || 0);
  return { x: viewportWidth - 28, y: 28 };
}

export function bindTheme(onTheme) {
  let themeRevealLocked = false;
  async function toggleThemeMode(event) {
    const root = document.documentElement;
    if (themeRevealLocked) return root.classList.contains("dark") ? "dark" : "light";
    const next = root.classList.contains("dark") ? "light" : "dark";
    const button = (event && event.currentTarget) || document.getElementById("themeModeButton");
    const reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const supportsViewTransition = typeof document.startViewTransition === "function";
    const applyDestination = () => {
      applyStoredThemeMode(next);
      document.cookie = "themeMode=" + next + "; Path=/; SameSite=Lax";
      onTheme(next);
    };
    const focusButton = () => {
      if (button && typeof button.focus === "function") button.focus({ preventScroll: true });
    };

    if (reducedMotion || !supportsViewTransition) {
      root.classList.add("theme-no-transition");
      try {
        applyDestination();
        void root.offsetWidth;
        return next;
      } finally {
        root.classList.remove("theme-no-transition");
        focusButton();
      }
    }

    const center = currentThemeIconCenter(button, root) || themeButtonCenter(button) || safeThemeRevealCenter();
    const radius = farthestThemeRevealRadius(center.x, center.y);
    const rootStyle = root.style;
    const setRevealProperty = (name, value) => {
      if (rootStyle && typeof rootStyle.setProperty === "function") rootStyle.setProperty(name, value);
      else if (rootStyle) rootStyle[name] = value;
    };
    const removeRevealProperty = (name) => {
      if (rootStyle && typeof rootStyle.removeProperty === "function") rootStyle.removeProperty(name);
      else if (rootStyle) delete rootStyle[name];
    };

    themeRevealLocked = true;
    root.classList.add("theme-transitioning");
    if (button) button.setAttribute("aria-busy", "true");
    setRevealProperty("--theme-reveal-x", center.x + "px");
    setRevealProperty("--theme-reveal-y", center.y + "px");
    setRevealProperty("--theme-reveal-radius", radius + "px");

    try {
      const transition = document.startViewTransition(() => {
        applyDestination();
      });
      await (transition && transition.finished ? transition.finished : Promise.resolve());
      return next;
    } finally {
      themeRevealLocked = false;
      root.classList.remove("theme-transitioning");
      if (button) {
        if (typeof button.removeAttribute === "function") button.removeAttribute("aria-busy");
        else if (button.attributes) delete button.attributes["aria-busy"];
      }
      removeRevealProperty("--theme-reveal-x");
      removeRevealProperty("--theme-reveal-y");
      removeRevealProperty("--theme-reveal-radius");
      focusButton();
    }
  }

  applyStoredThemeMode();
  document.getElementById("themeModeButton").onclick = toggleThemeMode;
}
