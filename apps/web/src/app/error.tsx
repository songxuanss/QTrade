"use client";

import { ErrorState } from "../components/ui/states";

export default function Error({
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <div className="workspace-page">
      <section className="panel">
        <ErrorState retry={retry} />
      </section>
    </div>
  );
}
