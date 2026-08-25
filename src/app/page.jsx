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
      <main>
        <ActionRunner />
      </main>
    </div>
  );
}
