"use client";

export const OPERATORS = {
  text: [["equals", "equals"], ["not_equals", "does not equal"], ["contains", "contains"], ["not_contains", "does not contain"], ["starts_with", "starts with"], ["ends_with", "ends with"], ["is_any_of", "is any of"], ["is_none_of", "is none of"]],
  blank: [["is_blank", "is blank"], ["is_not_blank", "is not blank"]],
  number: [["equals", "equals"], ["not_equals", "does not equal"], ["gt", "greater than"], ["gte", "greater than or equal"], ["lt", "less than"], ["lte", "less than or equal"], ["between", "between (inclusive)"]],
  date: [["on", "on"], ["before", "before"], ["after", "after"], ["between", "between (inclusive)"]],
};

export function newCondition(column, kind = "text") {
  return { column, kind, operator: OPERATORS[kind][0][0], value: kind === "blank" ? null : "", upper: null, values: [], ignore_case: false, date_format: "YYYY-MM-DD" };
}

const numberPattern = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
function validDate(value, format) {
  const pattern = format === "YYYY-MM-DD" ? /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/ : /^([0-9]{2})\/([0-9]{2})\/([0-9]{4})$/;
  const parts = pattern.exec(value);
  if (!parts) return false;
  const [year, month, day] = format === "YYYY-MM-DD" ? parts.slice(1).map(Number) : format === "MM/DD/YYYY" ? [Number(parts[3]), Number(parts[1]), Number(parts[2])] : [Number(parts[3]), Number(parts[2]), Number(parts[1])];
  const parsed = new Date(0); parsed.setUTCFullYear(year, month - 1, day); parsed.setUTCHours(0, 0, 0, 0);
  return year >= 1 && parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day;
}

// Compare decimal text without binary rounding or expanding scientific powers.
function compareNumbers(left, right) {
  function normalized(value) {
    const sign = value.startsWith("-") ? -1 : 1;
    const [mantissa, rawExponent = "0"] = value.replace(/^[+-]/, "").toLowerCase().split("e");
    const fractional = mantissa.split(".")[1]?.length ?? 0;
    const digits = mantissa.replace(".", "").replace(/^0+/, "");
    if (!digits) return { sign: 0, digits: "0", position: 0n };
    return { sign, digits, position: BigInt(digits.length - fractional) + BigInt(rawExponent) };
  }
  const a = normalized(left), b = normalized(right);
  if (a.sign !== b.sign) return Math.sign(a.sign - b.sign);
  if (!a.sign) return 0;
  if (a.position !== b.position) return (a.position > b.position ? 1 : -1) * a.sign;
  const width = Math.max(a.digits.length, b.digits.length);
  const x = a.digits.padEnd(width, "0"), y = b.digits.padEnd(width, "0");
  return (x === y ? 0 : x > y ? 1 : -1) * a.sign;
}
function dateKey(value, format) {
  if (format === "YYYY-MM-DD") return value;
  const [first, second, year] = value.split("/");
  return format === "MM/DD/YYYY" ? `${year}-${first}-${second}` : `${year}-${second}-${first}`;
}

export function conditionValid(condition) {
  if (condition.kind === "blank") return true;
  if (["is_any_of", "is_none_of"].includes(condition.operator)) return condition.values.length > 0 && condition.values.length <= 100;
  if (condition.kind === "text") return condition.value !== null && condition.value.length <= 10000;
  const values = condition.operator === "between" ? [condition.value, condition.upper] : [condition.value];
  const valid = values.every((value) => typeof value === "string" && (condition.kind === "number" ? numberPattern.test(value) : validDate(value, condition.date_format)));
  if (!valid || condition.operator !== "between") return valid;
  return condition.kind === "number" ? compareNumbers(condition.value, condition.upper) <= 0 : dateKey(condition.value, condition.date_format) <= dateKey(condition.upper, condition.date_format);
}

export default function FilterCondition({ condition, index, columns, onChange, onRemove, disabled }) {
  const membership = ["is_any_of", "is_none_of"].includes(condition.operator);
  const patch = (changes) => onChange({ ...condition, ...changes });
  function operatorChanged(operator) {
    const multiple = ["is_any_of", "is_none_of"].includes(operator);
    patch({ operator, value: condition.kind === "blank" || multiple ? null : condition.value ?? "", upper: operator === "between" ? "" : null, values: multiple ? [""] : [] });
  }
  return <fieldset disabled={disabled} className="csv-condition">
    <legend>Condition {index + 1}</legend>
    <div className="grid gap-3 sm:grid-cols-3">
      <label>Column<select className="form-control" aria-label={`Column ${index + 1}`} value={condition.column} onChange={(event) => patch({ column: event.target.value })}>{columns.map((column) => <option key={column} value={column}>{column || "(empty header)"}</option>)}</select></label>
      <label>Compare as<select className="form-control" aria-label={`Comparison type ${index + 1}`} value={condition.kind} onChange={(event) => onChange(newCondition(condition.column, event.target.value))}>{["text", "blank", "number", "date"].map((kind) => <option key={kind} value={kind}>{kind === "blank" ? "Blank values" : kind[0].toUpperCase() + kind.slice(1)}</option>)}</select></label>
      <label>Condition<select className="form-control" aria-label={`Operator ${index + 1}`} value={condition.operator} onChange={(event) => operatorChanged(event.target.value)}>{OPERATORS[condition.kind].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    </div>
    {condition.kind === "date" ? <label className="block mt-3">Date format in this column<select className="form-control" aria-label={`Date format ${index + 1}`} value={condition.date_format} onChange={(event) => patch({ date_format: event.target.value })}>{["YYYY-MM-DD", "MM/DD/YYYY", "DD/MM/YYYY"].map((format) => <option key={format}>{format}</option>)}</select></label> : null}
    {condition.kind !== "blank" && !membership ? <div className="grid gap-3 mt-3 sm:grid-cols-2">
      <label>{condition.operator === "between" ? "From (inclusive)" : "Value"}<input className="form-control" aria-label={`Value ${index + 1}`} value={condition.value ?? ""} maxLength={10000} placeholder={condition.kind === "date" ? condition.date_format : undefined} onChange={(event) => patch({ value: event.target.value })} /></label>
      {condition.operator === "between" ? <label>To (inclusive)<input className="form-control" aria-label={`Upper value ${index + 1}`} value={condition.upper ?? ""} maxLength={10000} placeholder={condition.kind === "date" ? condition.date_format : undefined} onChange={(event) => patch({ upper: event.target.value })} /></label> : null}
    </div> : null}
    {membership ? <div className="mt-3 space-y-2">
      {condition.values.map((value, valueIndex) => <div key={valueIndex} className="flex gap-2 items-center"><label className="grow">Text value {valueIndex + 1}<textarea className="form-control" rows={1} aria-label={`Selected value ${index + 1}.${valueIndex + 1}`} value={value} maxLength={10000} onChange={(event) => patch({ values: condition.values.map((item, n) => n === valueIndex ? event.target.value : item) })} /></label><button className="secondary-button" onClick={() => patch({ values: condition.values.filter((_, n) => n !== valueIndex) })}>Remove value {valueIndex + 1}</button></div>)}
      <button className="secondary-button" disabled={disabled || condition.values.length >= 100} onClick={() => patch({ values: [...condition.values, ""] })}>Add text value</button>
    </div> : null}
    <div className="flex flex-wrap gap-4 items-center mt-3">
      {condition.kind === "text" ? <label><input type="checkbox" checked={condition.ignore_case} onChange={(event) => patch({ ignore_case: event.target.checked })} /> Ignore case</label> : null}
      <button className="secondary-button" onClick={onRemove}>Remove condition {index + 1}</button>
    </div>
    {!conditionValid(condition) ? <p className="text-sm text-red-700 mt-2" role="status">Enter a valid {condition.kind === "date" ? condition.date_format + " date" : condition.kind === "number" ? "number (no spaces or commas)" : "text value"}. Between endpoints must be in ascending order.</p> : null}
  </fieldset>;
}
