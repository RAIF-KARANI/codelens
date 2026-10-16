/** The Aperture mark: a lens with a small code graph inside (planning-v2.md D141).
 * Drawn in `currentColor` under `text-primary`, so it is always the brand's
 * colour for the current theme. */
export function LogoMark({ className = "size-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={`text-primary ${className}`} aria-hidden="true">
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="3" />
      <path
        d="M11 19 L16 11 L21 19 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="11" r="2.6" fill="currentColor" />
      <circle cx="11" cy="19" r="2.6" fill="currentColor" />
      <circle cx="21" cy="19" r="2.6" fill="currentColor" />
    </svg>
  );
}

/** The mark and the wordmark; in a collapsed sidebar, the mark alone. */
export function Logo() {
  return (
    <span className="flex items-center gap-2">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight collapsed:hidden">CodeLens</span>
    </span>
  );
}
