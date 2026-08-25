"use client";

import { useEffect, useState } from "react";

import { fetchHealth } from "@/lib/api";

const PRESENTATION = {
  checking: { label: "Checking backend…", dot: "bg-zinc-400" },
  connected: { label: "Backend Connected", dot: "bg-emerald-500" },
  unavailable: { label: "Backend Unavailable", dot: "bg-red-500" },
};

/**
 * Small indicator showing whether the local FastAPI backend answers /health.
 *
 * The browser talks to the backend directly; requests are not proxied through
 * Next.js. This also exercises the backend CORS configuration.
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
