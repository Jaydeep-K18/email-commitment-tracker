/**
 * Light, dark, or follow the system.
 *
 * The preference lives in the server's settings (so it follows the user), and
 * is mirrored to localStorage so public/theme-init.js can apply it before the
 * first paint.
 */
import { useEffect } from "react";

export type ThemePreference = "system" | "light" | "dark";
const STORAGE_KEY = "commitmail.theme";

export function storedTheme(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(preference: ThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    /* private mode: the class below still applies for this session */
  }
  const dark =
    preference === "dark" ||
    (preference === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", !!dark);
}

/** Keep the page in step with the preference, and with the OS when it is "system". */
export function useThemeSync(preference: ThemePreference | undefined, reducedMotion: boolean | undefined): void {
  useEffect(() => {
    if (!preference) return;
    applyTheme(preference);
    if (preference !== "system" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => applyTheme("system");
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [preference]);

  useEffect(() => {
    document.documentElement.dataset.reducedMotion = reducedMotion ? "true" : "false";
  }, [reducedMotion]);
}
