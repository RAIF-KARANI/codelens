"use client";

import { useLayoutEffect, useSyncExternalStore } from "react";

import {
  DARK_QUERY,
  THEME_KEY,
  parseThemeChoice,
  resolveTheme,
  type Theme,
  type ThemeChoice,
} from "@/lib/theme";

/** Fired on this tab when the choice changes; other tabs get `storage`. */
const CHANGED = "codelens-theme-change";

function readChoice(): ThemeChoice {
  try {
    return parseThemeChoice(localStorage.getItem(THEME_KEY));
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const theme = resolveTheme(choice, matchMedia(DARK_QUERY).matches);
  document.documentElement.setAttribute("data-theme", theme);
}

function subscribeChoice(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Save a choice and show it now. */
export function setThemeChoice(choice: ThemeChoice) {
  try {
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Storage blocked: the choice still applies to this page.
  }
  apply(choice);
  window.dispatchEvent(new Event(CHANGED));
}

/** The stored choice, kept applied: follows the system while the choice is
 * "system", follows other tabs, and re-applies after React's development
 * remount resets <html> (the theme script only runs once, at parse time). */
export function useThemeChoice(): ThemeChoice {
  const choice = useSyncExternalStore(subscribeChoice, readChoice, () => "system" as const);
  useLayoutEffect(() => {
    apply(choice);
    if (choice !== "system") return;
    const media = matchMedia(DARK_QUERY);
    const follow = () => apply("system");
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [choice]);
  return choice;
}

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}

function readTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/** The theme on screen right now, for components that style themselves in
 * script (toasts, the graph). */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, readTheme, () => "light" as const);
}
