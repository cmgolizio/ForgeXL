import BackendStatus from "@/components/backend/BackendStatus";

export default function Home() {
  return (
    <div className='flex flex-1 items-center justify-center px-6 py-24'>
      <main className='flex w-full max-w-2xl flex-col items-start gap-4'>
        <h1 className='text-3xl font-semibold tracking-tight'>
          Local Data Workbench
        </h1>
        <p className='text-lg text-zinc-600 dark:text-zinc-400'>
          Local data-processing proof of concept.
        </p>
        <BackendStatus />
      </main>
    </div>
  );
}
