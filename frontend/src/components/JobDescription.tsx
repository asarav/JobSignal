interface JobDescriptionProps {
  value?: string | null;
}

/**
 * Posting text is always rendered as text, never as HTML.
 *
 * This is the XSS control: the app stores untrusted descriptions as strings
 * and React escapes them. Nothing here may use ``dangerouslySetInnerHTML``.
 */
export function JobDescription({ value }: JobDescriptionProps) {
  if (!value) {
    return (
      <p className="mt-2 text-sm text-slate-500">
        No description was stored for this posting. Open the original to read it.
      </p>
    );
  }
  return (
    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
      {value}
    </p>
  );
}
