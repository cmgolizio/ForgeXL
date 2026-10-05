import Link from "next/link";
import BackendStatus from "@/components/backend/BackendStatus";
import MonthlyReports from "@/components/monthly/MonthlyReports";

export const metadata = { title: "Monthly Sales Rep Reports · ForgeXL" };

export default function MonthlyReportsPage() {
  return <div className="app-shell">
    <header className="app-header"><Link href="/" className="brand"><span aria-hidden="true">F</span>ForgeXL</Link><BackendStatus /></header>
    <div className="page-intro"><Link href="/" className="back-link">← Change action</Link><h1>Monthly sales rep reports</h1><p>Every rep’s Excel report. One download.</p></div>
    <MonthlyReports />
    <footer className="app-footer">Sales follow the salesperson on each invoice · Saved history is reused automatically</footer>
  </div>;
}
