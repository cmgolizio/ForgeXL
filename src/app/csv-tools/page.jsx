import Link from "next/link";
import BackendStatus from "@/components/backend/BackendStatus";
import CSVTools from "@/components/csv/CSVTools";

export const metadata = { title: "CSV tools · ForgeXL" };

export default async function CSVToolsPage({ searchParams }) {
  const { action } = await searchParams;
  return <div className="app-shell">
    <header className="app-header"><Link href="/" className="brand"><span aria-hidden="true">F</span>ForgeXL</Link><BackendStatus /></header>
    <div className="page-intro"><Link href="/" className="back-link">← All actions</Link><h1>CSV tools</h1><p>Combine, filter, and download your data.</p></div>
    <main><CSVTools initialActionId={typeof action === "string" ? action : ""} /></main>
    <footer className="app-footer">Processed on this machine · Saved monthly history is untouched</footer>
  </div>;
}
