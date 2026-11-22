/** A row from `resume.list_versions` (stored columns, parsed blob included). */
export interface ResumeVersionRow {
  id: string;
  label?: string;
  parser_id?: string;
  is_active?: boolean | number;
  created_at?: string;
  parsed_json?: string | null;
  original_name?: string;
  stored_path?: string | null;
}

/** Narrows an IPC row to the fields the viewer needs; `null` when unusable. */
export function toVersionRow(raw: Record<string, unknown>): ResumeVersionRow | null {
  const id = typeof raw.id === "string" ? raw.id : "";
  if (id === "") return null;
  return {
    id,
    label: typeof raw.label === "string" ? raw.label : undefined,
    parser_id: typeof raw.parser_id === "string" ? raw.parser_id : undefined,
    is_active: Boolean(raw.is_active),
    created_at: typeof raw.created_at === "string" ? raw.created_at : undefined,
    parsed_json: typeof raw.parsed_json === "string" ? raw.parsed_json : null,
    original_name: typeof raw.original_name === "string" ? raw.original_name : undefined,
    stored_path: typeof raw.stored_path === "string" ? raw.stored_path : null,
  };
}

export function isActiveVersion(row: ResumeVersionRow): boolean {
  return Boolean(row.is_active);
}

/** Extracted text of a parsed version, or `null` when nothing was stored. */
export function parsedText(row: ResumeVersionRow): string | null {
  const raw = row.parsed_json;
  if (typeof raw !== "string" || raw.trim() === "") return null;
  try {
    const parsed = JSON.parse(raw) as { text?: unknown };
    return typeof parsed.text === "string" ? parsed.text : null;
  } catch {
    return null;
  }
}

/** Oldest first, so "previous version" means the one imported before it. */
export function orderedVersions(rows: ResumeVersionRow[]): ResumeVersionRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftTime = Date.parse(left.row.created_at ?? "") || 0;
      const rightTime = Date.parse(right.row.created_at ?? "") || 0;
      return leftTime - rightTime || left.index - right.index;
    })
    .map((entry) => entry.row);
}

export function previousVersion(
  rows: ResumeVersionRow[],
  versionId: string,
): ResumeVersionRow | null {
  const ordered = orderedVersions(rows);
  const index = ordered.findIndex((row) => row.id === versionId);
  return index > 0 ? ordered[index - 1] : null;
}

export function versionLabel(row: ResumeVersionRow): string {
  return row.label?.trim() || row.id;
}
