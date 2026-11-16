import { useParams } from "react-router-dom";
import { Copy, type PlaceholderKey } from "../../copy/copy";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";

export function PlaceholderRoute({ screen }: { screen: PlaceholderKey }) {
  const params = useParams();
  const entry = Copy.placeholders[screen];
  const title = params.id ? `${entry.title}: ${params.id}` : entry.title;

  return (
    <div className="space-y-6">
      <PageHeader title={title} description={entry.body} />
      <EmptyState heading={Copy.emptyState.heading} body={Copy.emptyState.body} />
    </div>
  );
}
