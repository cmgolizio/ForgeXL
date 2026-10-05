import Link from "next/link";
import BackendStatus from "@/components/backend/BackendStatus";
import ActionRunner from "@/components/workbench/ActionRunner";

export default function Home() {
  return <div className="app-shell">
    <header className="app-header"><Link href="/" className="brand"><span aria-hidden="true">F</span>ForgeXL</Link><BackendStatus /></header>
    <div className="page-intro"><p className="eyebrow">YOUR DATA. READY TO USE.</p><h1>What would you like to create?</h1><p>Choose an action, add your files, and let ForgeXL do the rest.</p></div>
    <main><ActionRunner /></main>
    <footer className="app-footer">CSV & Excel files · Processed on your ForgeXL computer</footer>
  </div>;
}
