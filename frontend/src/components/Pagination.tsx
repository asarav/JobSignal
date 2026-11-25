import { Button } from "./Button";

export function Pagination({
  shown,
  total,
  onLoadMore,
  loading = false,
  noun = "items",
  pageSize = 25,
}: {
  shown: number;
  total: number;
  onLoadMore?: () => void;
  loading?: boolean;
  noun?: string;
  pageSize?: number;
}) {
  if (total === 0) return null;
  const hasMore = shown < total;
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
      <p className="tabular text-xs text-slate-500">
        Showing {Math.min(shown, total)} of {total} {noun}
      </p>
      {hasMore && onLoadMore ? (
        <Button variant="secondary" onClick={onLoadMore} disabled={loading}>
          {loading ? "Loading…" : `Load ${Math.min(pageSize, total - shown)} more`}
        </Button>
      ) : null}
    </div>
  );
}
