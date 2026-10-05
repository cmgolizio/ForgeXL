"use client";

import Link from "next/link";

export default function ActionSelector({ actions, selectedActionId, onSelect, disabled = false }) {
  return <div className="action-grid" aria-label="Choose an action">
    {actions.map((action) => {
      const content = <><span className="action-card-top"><span className="action-symbol" aria-hidden="true">{action.workflow_path ? "▤" : "↗"}</span><span aria-hidden="true">→</span></span><strong>{action.name}</strong><span>{action.description}</span></>;
      return action.workflow_path && !disabled ? <Link key={action.id} className="action-card" href={action.workflow_path}>{content}</Link> : <button key={action.id} type="button" className="action-card" aria-pressed={selectedActionId === action.id} disabled={disabled} onClick={() => onSelect(action.id)}>{content}</button>;
    })}
  </div>;
}
