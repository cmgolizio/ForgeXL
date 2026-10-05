export default function WorkflowSteps({ current = 1 }) {
  return <ol className="workflow-steps" aria-label="Report workflow">
    {["Choose action", "Upload files", "Generate report"].map((label, index) => <li key={label} aria-current={current === index + 1 ? "step" : undefined} data-complete={current > index + 1}>
      <span>{current > index + 1 ? "✓" : index + 1}</span>{label}
    </li>)}
  </ol>;
}

export function StepHeading({ number, title, children }) {
  return <div className="step-heading"><span className="step-number">{number}</span><div><h2>{title}</h2>{children ? <p>{children}</p> : null}</div></div>;
}
