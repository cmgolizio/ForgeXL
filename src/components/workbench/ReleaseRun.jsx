"use client";

import { useRef, useState } from "react";
import { discardRun } from "@/lib/api";

/** Explicit cleanup of this result only; never deletes stored sources. */
export default function ReleaseRun({ runId, onReleased, disabled = false }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const working = useRef(false);

  async function release() {
    if (working.current) return;
    working.current = true; setBusy(true); setError(null);
    try {
      await discardRun(runId);
      onReleased();
    } catch (cause) {
      // A restart or another tab may already have released this result.
      if (cause?.status === 404) onReleased();
      else setError(cause?.message || "Could not release this result. Please retry.");
    } finally { working.current = false; setBusy(false); }
  }

  return <div className="flex flex-col items-start gap-2">
    <p className="text-xs text-zinc-500">After downloading, release this result to free its server memory. Stored sources and saved reporting cycles are not deleted. These download links will stop working.</p>
    <button type="button" className="rounded-lg border border-zinc-300 px-4 py-2 text-sm disabled:opacity-50 dark:border-zinc-700" disabled={disabled || busy} onClick={release}>{busy ? "Releasing…" : "Release preview and downloads"}</button>
    {error ? <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p> : null}
  </div>;
}
