const PALETTE = [
  "bg-indigo-100 text-indigo-700",
  "bg-teal-100 text-teal-700",
  "bg-amber-100 text-amber-800",
  "bg-rose-100 text-rose-700",
  "bg-sky-100 text-sky-700",
  "bg-violet-100 text-violet-700",
];

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}

/** Deterministic company mark, so familiar employers stay recognisable. */
export function CompanyAvatar({
  name,
  size = "md",
}: {
  name?: string | null;
  size?: "sm" | "md" | "lg";
}) {
  const label = name?.trim() || "Unknown company";
  let hash = 0;
  for (const char of label) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const sizeClass =
    size === "sm" ? "h-8 w-8 text-xs" : size === "lg" ? "h-12 w-12 text-base" : "h-10 w-10 text-sm";
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-lg font-bold ${sizeClass} ${PALETTE[hash % PALETTE.length]}`}
      title={label}
      aria-hidden="true"
    >
      {initials(label)}
    </span>
  );
}
