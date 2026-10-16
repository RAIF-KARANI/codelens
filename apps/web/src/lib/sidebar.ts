/** Whether the sidebar is collapsed to its icons. Like the theme (lib/theme.ts),
 * the choice lives in localStorage and an inline script applies it before the
 * first paint, as `data-sidebar="collapsed"` on <html>; CSS does the rest
 * through the `collapsed:` variant (globals.css), so no page reads a cookie. */

export const SIDEBAR_KEY = "codelens-sidebar";

export const SIDEBAR_SCRIPT = `(function(){try{if(localStorage.getItem(${JSON.stringify(SIDEBAR_KEY)})==="collapsed")document.documentElement.setAttribute("data-sidebar","collapsed")}catch(e){}})()`;
