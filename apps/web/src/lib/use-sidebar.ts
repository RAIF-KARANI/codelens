"use client";

import { useLayoutEffect, useSyncExternalStore } from "react";

import { SIDEBAR_KEY } from "@/lib/sidebar";

const CHANGED = "codelens-sidebar-change";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function apply(collapsed: boolean) {
  if (collapsed) document.documentElement.setAttribute("data-sidebar", "collapsed");
  else document.documentElement.removeAttribute("data-sidebar");
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function setSidebarCollapsed(collapsed: boolean) {
  try {
    if (collapsed) localStorage.setItem(SIDEBAR_KEY, "collapsed");
    else localStorage.removeItem(SIDEBAR_KEY);
  } catch {
    // Storage blocked: it still applies to this page.
  }
  apply(collapsed);
  window.dispatchEvent(new Event(CHANGED));
}

/** Whether the sidebar is collapsed, kept applied after React's development
 * remount resets <html> (as with the theme, lib/use-theme.ts). */
export function useSidebarCollapsed(): boolean {
  const collapsed = useSyncExternalStore(subscribe, readCollapsed, () => false);
  useLayoutEffect(() => apply(collapsed), [collapsed]);
  return collapsed;
}
