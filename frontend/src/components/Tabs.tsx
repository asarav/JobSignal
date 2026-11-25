export interface TabItem {
  id: string;
  label: string;
  count?: number;
}

export function Tabs({
  items,
  active,
  onChange,
  label,
}: {
  items: TabItem[];
  active: string;
  onChange: (id: string) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-slate-200">
      {items.map((item) => {
        const selected = item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.id)}
            className={`-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition ${
              selected
                ? "border-brand-600 text-brand-700"
                : "border-transparent text-slate-500 hover:border-brand-200 hover:text-slate-800"
            }`}
          >
            {item.label}
            {typeof item.count === "number" ? (
              <span className={`ml-1.5 rounded-full px-1.5 py-0.5 text-xs ${selected ? "bg-brand-100 text-brand-700" : "bg-slate-100 text-slate-600"}`}>
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
