import { Copy } from "../../copy/copy";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { useWorkerStore } from "../../stores/workerStore";

export function HomeRoute() {
  const health = useWorkerStore((state) => state.health);
  const hello = useWorkerStore((state) => state.hello);

  return (
    <div className="space-y-6">
      <PageHeader title={Copy.home.title} description={Copy.home.subtitle} />

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.home.capabilities}</h2>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">{Copy.home.welcome}</p>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-slate-500">
              {Copy.home.health}
              <InfoTip label={Copy.home.health}>{Copy.tooltips.health}</InfoTip>
            </dt>
            <dd className="font-medium text-slate-900">{health?.status ?? Copy.settings.none}</dd>
          </div>
          <div>
            <dt className="text-slate-500">{Copy.home.version}</dt>
            <dd className="font-medium text-slate-900">
              {hello?.app_version ?? health?.app_version ?? Copy.settings.none}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">
              {Copy.settings.schemaVersion}
              <InfoTip label={Copy.settings.schemaVersion}>{Copy.tooltips.schema}</InfoTip>
            </dt>
            <dd className="font-medium text-slate-900">
              {health?.schema_version ?? Copy.settings.none}
            </dd>
          </div>
        </dl>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.home.notImplemented}</h2>
        <p className="mt-1 text-sm text-slate-600">
          These features are not part of this build, so the screens that would show
          them stay empty.
        </p>
        <ul className="mt-2 grid gap-1 text-sm text-slate-600 sm:grid-cols-2">
          {Copy.home.notImplementedList.map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span aria-hidden="true" className="text-slate-400">
                •
              </span>
              {item}
            </li>
          ))}
        </ul>
      </section>

    </div>
  );
}
