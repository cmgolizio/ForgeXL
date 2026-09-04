"use client";

import { useEffect, useState } from "react";

import { fetchHealth } from "@/lib/api";

const PRESENTATION = {
  checking: { label: "Checking backend…", dot: "bg-zinc-400" },
  connected: { label: "Backend Connected", dot: "bg-emerald-500" },
  unavailable: { label: "Backend Unavailable", dot: "bg-red-500" },
};

/**
 * Small indicator showing whether the ForgeXL backend answers /health.
 *
 * The request is same-origin — `/forge-api/health` — and the Route Handler at
 * that path forwards it to FastAPI (build plan 6G.2/6G.3). So this reports on
 * the backend of whichever machine served the page, which is what makes it
 * meaningful when the page is open on a second laptop. A backend that is not
 * running answers 502 through that handler, which `lib/api.js` renders as
 * "Backend Unavailable" rather than as a raw failure.
 */
export default function BackendStatus() {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    const controller = new AbortController();

    async function checkHealth() {
      try {
        const healthy = await fetchHealth({ signal: controller.signal });
        setStatus(healthy ? "connected" : "unavailable");
      } catch {
        if (controller.signal.aborted) return;
        setStatus("unavailable");
      }
    }

    checkHealth();
    return () => controller.abort();
  }, []);

  const { label, dot } = PRESENTATION[status];

  return (
    <p
      className='inline-flex items-center gap-2 rounded-full border border-zinc-200 px-3 py-1.5 text-sm text-zinc-600 dark:border-zinc-800 dark:text-zinc-400'
      aria-live='polite'
    >
      <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden='true' />
      {label}
    </p>
  );
}
