import Link from "next/link";
import BackendStatus from "@/components/backend/BackendStatus";
import ActionRunner from "@/components/workbench/ActionRunner";

export default function Home() {
  return (
    <div className='mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-12'>
      <header className='flex flex-col items-start gap-3'>
        <h1 className='text-3xl font-semibold tracking-tight'>
          Local Data Workbench
        </h1>
        <p className='text-lg text-zinc-600 dark:text-zinc-400'>
          Run reusable data-processing Actions locally.
        </p>
        <BackendStatus />
      </header>
      <Link href="/monthly-reports" className="rounded-lg border border-zinc-300 p-4 text-lg font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900">
        Monthly Reports →
        <span className="mt-1 block text-sm font-normal text-zinc-500">Validate monthly sources, generate rep workbooks, and rerun saved periods.</span>
      </Link>
      <main>
        <ActionRunner />
      </main>
    </div>
  );
}
