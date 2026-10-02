import Link from "next/link";
import BackendStatus from "@/components/backend/BackendStatus";
import MonthlyReports from "@/components/monthly/MonthlyReports";

export const metadata = { title: "Monthly Reports · ForgeXL" };

export default function MonthlyReportsPage() {
  return <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-8 px-6 py-10">
    <header className="flex flex-col items-start gap-3">
      <Link href="/" className="text-sm text-zinc-500 hover:underline">← All Actions</Link>
      <h1 className="text-3xl font-semibold tracking-tight">Monthly Reports</h1>
      <p className="text-zinc-600 dark:text-zinc-400">Upload this month’s sources, review validation, then generate every rep’s workbook.</p>
      <BackendStatus />
    </header>
    <MonthlyReports />
  </div>;
}
