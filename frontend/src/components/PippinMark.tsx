/** Pippin's mark: a p drawn in one stroke with a sprout at the top of its stem, on a soft square tile.
 *  The tile takes the brightest text colour and the p the page's, so it turns over with the theme. The same drawing is
 *  in public/favicon.svg, the extension's and the desktop app's icons, and the desktop orb. */
export default function PippinMark({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <rect width="100" height="100" rx="30" fill="var(--fg-bright)" />
      <path d="M38 82V50A17 17 0 1 1 43 62" stroke="var(--bg)" strokeWidth={size < 24 ? 12 : 10} fill="none"
        strokeLinecap="round" strokeLinejoin="round" />
      <path d="M36 40c-1-9-7-15-16-15 0 9 7 14 16 15z" fill="var(--bg)" />
    </svg>
  )
}
