
export function EmptyState({ heading, body }: { heading: string; body: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
      <p className="text-sm font-semibold text-slate-700">{heading}</p>
      <p className="mx-auto mt-1 max-w-xl text-sm text-slate-500">{body}</p>
    </div>
  );
}
