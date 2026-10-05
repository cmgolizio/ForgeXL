"use client";

export default function RunButton({ onRun, disabled = false, running = false }) {
  return <button type="button" onClick={onRun} disabled={disabled || running} aria-busy={running} className="primary-button">
    {running ? "Generating your report…" : "Generate report"}<span aria-hidden="true">→</span>
  </button>;
}
