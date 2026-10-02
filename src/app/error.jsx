"use client";

export default function Error({ reset }) {
  return <main className="mx-auto flex max-w-3xl flex-col gap-4 p-6" role="alert">
    <h1 className="text-xl font-semibold">ForgeXL could not display this page.</h1>
    <p>Your saved source versions are separate from this page. Retry, then use a saved reporting cycle to recreate downloads if needed.</p>
    <button onClick={() => reset()} className="self-start rounded-lg border px-4 py-2">Try again</button>
    <a href="/monthly-reports" className="underline">Return to Monthly Reports</a>
  </main>;
}
