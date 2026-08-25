"use client";

import { useId, useState } from "react";

import { fileExtension, formatFileSize, joinWithOr } from "@/lib/formatters";

/**
 * One upload control for one Action input slot (build plan 5.5, 5.6).
 *
 * Everything rendered here comes from the slot definition the backend
 * returned — label, description, accepted extensions, required flag, required
 * columns. There is no branch on any Action ID, so an Action declaring three
 * inputs simply renders three of these.
 *
 * A file can be chosen by clicking or by dropping, and can be replaced or
 * removed before the Run starts. Only native browser APIs are used.
 */
export default function FileUploadSlot({
  input,
  file,
  error,
  disabled = false,
  onSelect,
  onRemove,
}) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  const accepted = input.accepted_extensions ?? [];
  const requiredColumns = input.required_columns ?? [];

  function handleDragOver(event) {
    event.preventDefault();
    if (disabled) return;
    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onSelect(dropped);
  }

  function handleChange(event) {
    const chosen = event.target.files?.[0];
    if (chosen) onSelect(chosen);
    // Clearing the control lets the same file be chosen again after a removal,
    // which otherwise fires no change event.
    event.target.value = "";
  }

  const dropZoneTone = dragging
    ? "border-zinc-900 bg-zinc-100 dark:border-zinc-100 dark:bg-zinc-800"
    : "border-zinc-300 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-600";

  return (
    <div className='flex flex-col gap-2'>
      <div className='flex flex-wrap items-baseline gap-x-2'>
        <span className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
          {input.label}
        </span>
        <span className='text-xs text-zinc-500 dark:text-zinc-500'>
          {input.required ? "Required" : "Optional"}
        </span>
      </div>

      {input.description ? (
        <p className='text-xs text-zinc-600 dark:text-zinc-400'>
          {input.description}
        </p>
      ) : null}

      <input
        id={inputId}
        type='file'
        accept={accepted.join(",")}
        disabled={disabled}
        onChange={handleChange}
        className='peer sr-only'
      />
      <label
        htmlFor={inputId}
        onDragEnter={handleDragOver}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-6 text-center transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-zinc-900 dark:peer-focus-visible:ring-zinc-100 ${dropZoneTone} ${
          disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"
        }`}
      >
        <span className='text-sm text-zinc-700 dark:text-zinc-300'>
          Drop file here or click to choose
        </span>
        <span className='text-xs text-zinc-500 dark:text-zinc-500'>
          {joinWithOr(accepted)}
        </span>
      </label>

      {requiredColumns.length > 0 ? (
        <p className='text-xs text-zinc-500 dark:text-zinc-500'>
          Required columns: {requiredColumns.join(", ")}
        </p>
      ) : null}

      {file ? (
        <div className='flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800'>
          <div className='flex min-w-0 flex-col'>
            <span className='truncate text-sm text-zinc-900 dark:text-zinc-100'>
              {file.name}
            </span>
            <span className='text-xs text-zinc-500 dark:text-zinc-500'>
              {fileExtension(file.name) || "no extension"} ·{" "}
              {formatFileSize(file.size)}
            </span>
          </div>
          <button
            type='button'
            onClick={onRemove}
            disabled={disabled}
            className='shrink-0 rounded-md border border-zinc-300 px-2 py-1 text-xs text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800'
          >
            Remove
          </button>
        </div>
      ) : null}

      {error ? (
        <p role='alert' className='text-xs text-red-600 dark:text-red-400'>
          {error}
        </p>
      ) : null}
    </div>
  );
}
