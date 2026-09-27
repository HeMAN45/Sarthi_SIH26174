/** The SARTHI mark: a chariot wheel, drawn in one line. A sarthi is the
 *  charioteer - the one who guides. Takes the current accent colour. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"
         style={{ color: "var(--accent)", flex: "none" }}>
      <circle cx="16" cy="16" r="13" />
      <circle cx="16" cy="16" r="3.4" />
      <path d="M16 3v9.6M16 19.4V29M3 16h9.6M19.4 16H29M6.8 6.8l6.8 6.8M18.4 18.4l6.8 6.8M25.2 6.8l-6.8 6.8M13.6 18.4l-6.8 6.8" />
    </svg>
  );
}
