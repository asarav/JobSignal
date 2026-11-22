export type DiffKind = "equal" | "added" | "removed";

export interface DiffLine {
  kind: DiffKind;
  text: string;
}

export interface DiffSummary {
  added: number;
  removed: number;
}

function toLines(text: string): string[] {
  if (text.trim() === "") return [];
  return text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
}

/**
 * Line-level diff (longest common subsequence). Resume text is small, so the
 * quadratic table is cheap and avoids a dependency.
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = toLines(before);
  const b = toLines(after);
  const table: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ kind: "equal", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      lines.push({ kind: "removed", text: a[i] });
      i += 1;
    } else {
      lines.push({ kind: "added", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    lines.push({ kind: "removed", text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    lines.push({ kind: "added", text: b[j] });
    j += 1;
  }
  return lines;
}

export function summarizeDiff(lines: DiffLine[]): DiffSummary {
  return lines.reduce<DiffSummary>(
    (summary, line) => {
      if (line.kind === "added") summary.added += 1;
      if (line.kind === "removed") summary.removed += 1;
      return summary;
    },
    { added: 0, removed: 0 },
  );
}
