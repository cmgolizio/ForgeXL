"use client";

import { useEffect, useState } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

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
        const response = await fetch(`${API_BASE_URL}/health`, {
          signal: controller.signal,
          cache: "no-store",
        });
        const payload = response.ok ? await response.json() : null;
        setStatus(payload?.status === "ok" ? "connected" : "unavailable");
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
