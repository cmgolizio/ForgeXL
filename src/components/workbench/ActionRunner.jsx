"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ActionDescription from "@/components/workbench/ActionDescription";
import ActionSelector from "@/components/workbench/ActionSelector";
import AuditSummary from "@/components/workbench/AuditSummary";
import DataPreview from "@/components/workbench/DataPreview";
import FileUploadSlot from "@/components/workbench/FileUploadSlot";
import OutputSelector from "@/components/workbench/OutputSelector";
import ResultsSummary from "@/components/workbench/ResultsSummary";
import RunButton from "@/components/workbench/RunButton";
import RunStatus from "@/components/workbench/RunStatus";
import { ApiError, createRun, fetchActions } from "@/lib/api";
import { fileExtension, joinWithOr } from "@/lib/formatters";

/**
 * The Action workflow: choose, upload, run, see what happened (build plan Phase 5).
 *
 * This component owns all of the interface state and knows nothing about any
 * particular Action. Every control below is generated from the metadata
 * `GET /api/actions` returned, which is what lets a new backend Action appear
 * without a frontend change (build plan sections 3.2 and 32).
 *
 * The state names follow build plan section 30: loading_actions, idle, ready,
 * running, success, validation_error, server_error.
 */
export default function ActionRunner() {
  const [actions, setActions] = useState([]);
  const [actionsStatus, setActionsStatus] = useState("loading");
  const [actionsError, setActionsError] = useState(null);

  const [selectedActionId, setSelectedActionId] = useState("");
  const [files, setFiles] = useState({});
  const [slotErrors, setSlotErrors] = useState({});

  const [runStatus, setRunStatus] = useState("idle");
  const [runError, setRunError] = useState(null);
  const [manifest, setManifest] = useState(null);
  const [selectedOutputId, setSelectedOutputId] = useState("");

  // Guards against a second submission slipping through between the click and
  // the re-render that disables the button.
  const inFlight = useRef(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadActions() {
      try {
        const loaded = await fetchActions({ signal: controller.signal });
        setActions(loaded);
        setActionsStatus("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        setActionsError(asApiError(error));
        setActionsStatus("error");
      }
    }

    loadActions();
    return () => controller.abort();
  }, []);

  const selectedAction = useMemo(
    () => actions.find((action) => action.id === selectedActionId) ?? null,
    [actions, selectedActionId],
  );

  const missingRequiredSlots = useMemo(
    () =>
      (selectedAction?.inputs ?? []).filter(
        (input) => input.required && !files[input.id],
      ),
    [selectedAction, files],
  );

  const running = runStatus === "running";

  // Build plan section 30: the Run button is disabled when no Action is
  // selected, when a required file is missing, or while a Run is executing.
  const canRun =
    selectedAction !== null && missingRequiredSlots.length === 0 && !running;

  const state = useMemo(() => {
    if (actionsStatus === "loading") return "loading_actions";
    if (actionsStatus === "error") return "server_error";
    if (running) return "running";
    if (runStatus === "success") return "success";
    if (runStatus === "error") {
      // 413 and 422 are the statuses the backend uses for a problem with what
      // was uploaded (build plan section 22); anything else is ours, not the
      // user's.
      return runError?.status === 422 || runError?.status === 413
        ? "validation_error"
        : "server_error";
    }
    if (!selectedAction) return "idle";
    return missingRequiredSlots.length === 0 ? "ready" : "idle";
  }, [
    actionsStatus,
    running,
    runStatus,
    runError,
    selectedAction,
    missingRequiredSlots,
  ]);

  /**
   * The result table currently on screen.
   *
   * Resolved from the manifest rather than stored alongside it, so a stale ID
   * from a previous Run can never select a table this Run does not have.
   */
  const outputs = useMemo(() => manifest?.outputs ?? [], [manifest]);

  const selectedOutput = useMemo(
    () =>
      outputs.find((output) => output.id === selectedOutputId) ??
      outputs[0] ??
      null,
    [outputs, selectedOutputId],
  );

  /** Clear a finished Run once the inputs it described no longer apply. */
  const clearRunResult = useCallback(() => {
    setRunStatus("idle");
    setRunError(null);
    setManifest(null);
    setSelectedOutputId("");
  }, []);

  const handleSelectAction = useCallback(
    (actionId) => {
      setSelectedActionId(actionId);
      setFiles({});
      setSlotErrors({});
      clearRunResult();
    },
    [clearRunResult],
  );

  /**
   * Accept a chosen file for one slot, after the convenience check of build
   * plan 5.7. The extension check here only saves a round trip; the backend
   * checks it again and remains authoritative.
   */
  const handleSelectFile = useCallback(
    (input, file) => {
      const accepted = input.accepted_extensions ?? [];
      const extension = fileExtension(file.name);

      if (!accepted.includes(extension)) {
        setFiles((current) => omit(current, input.id));
        setSlotErrors((current) => ({
          ...current,
          [input.id]: `${input.label} must be ${joinWithOr(accepted)}.`,
        }));
        clearRunResult();
        return;
      }

      setFiles((current) => ({ ...current, [input.id]: file }));
      setSlotErrors((current) => omit(current, input.id));
      clearRunResult();
    },
    [clearRunResult],
  );

  const handleRemoveFile = useCallback(
    (input) => {
      setFiles((current) => omit(current, input.id));
      setSlotErrors((current) => omit(current, input.id));
      clearRunResult();
    },
    [clearRunResult],
  );

  async function handleRun() {
    if (inFlight.current || !selectedAction) return;

    if (missingRequiredSlots.length > 0) {
      setSlotErrors((current) => ({
        ...current,
        ...Object.fromEntries(
          missingRequiredSlots.map((input) => [
            input.id,
            `${input.label} is required.`,
          ]),
        ),
      }));
      return;
    }

    inFlight.current = true;
    setRunStatus("running");
    setRunError(null);
    setManifest(null);

    try {
      // Files are keyed by Action input slot ID and reach the backend under
      // exactly those names (build plan 5.8).
      const result = await createRun({ actionId: selectedAction.id, files });
      setManifest(result);
      // The Action decides which table is primary by declaring it first.
      setSelectedOutputId(result?.outputs?.[0]?.id ?? "");
      setRunStatus("success");
    } catch (error) {
      setRunError(asApiError(error));
      setRunStatus("error");
    } finally {
      inFlight.current = false;
    }
  }

  if (state === "loading_actions") {
    return (
      <p className='text-sm text-zinc-600 dark:text-zinc-400'>
        Loading Actions…
      </p>
    );
  }

  if (actionsStatus === "error") {
    return (
      <section
        role='alert'
        className='flex flex-col gap-1 rounded-lg border border-red-300 bg-red-50 px-4 py-3 dark:border-red-900 dark:bg-red-950/40'
      >
        <h2 className='text-sm font-semibold text-red-900 dark:text-red-200'>
          Actions Unavailable
        </h2>
        <p className='text-sm text-red-800 dark:text-red-300'>
          {actionsError?.message}
        </p>
      </section>
    );
  }

  return (
    <div data-workbench-state={state} className='flex flex-col gap-6'>
      <ActionSelector
        actions={actions}
        selectedActionId={selectedActionId}
        onSelect={handleSelectAction}
        disabled={running}
      />

      <ActionDescription action={selectedAction} />

      {selectedAction ? (
        <section className='flex flex-col gap-4'>
          <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
            Required Inputs
          </h3>
          {selectedAction.inputs.map((input) => (
            <FileUploadSlot
              key={input.id}
              input={input}
              file={files[input.id] ?? null}
              error={slotErrors[input.id] ?? null}
              disabled={running}
              onSelect={(file) => handleSelectFile(input, file)}
              onRemove={() => handleRemoveFile(input)}
            />
          ))}
        </section>
      ) : null}

      <RunButton onRun={handleRun} running={running} disabled={!canRun} />

      <RunStatus state={state} error={runError} manifest={manifest} />

      {state === "success" && manifest && selectedOutput ? (
        <div className='flex flex-col gap-6 border-t border-zinc-200 pt-6 dark:border-zinc-800'>
          <OutputSelector
            outputs={outputs}
            selectedOutputId={selectedOutput.id}
            onSelect={setSelectedOutputId}
          />

          <ResultsSummary manifest={manifest} output={selectedOutput} />

          <DataPreview
            // A different Run or result table is a different preview: the key
            // remounts it so paging starts again at the first page.
            key={`${manifest.run_id}:${selectedOutput.id}`}
            runId={manifest.run_id}
            outputId={selectedOutput.id}
            label={outputs.length > 1 ? selectedOutput.label : null}
          />

          <AuditSummary manifest={manifest} />
        </div>
      ) : null}
    </div>
  );
}

/** Return a copy of `record` without `key`. */
function omit(record, key) {
  const { [key]: _removed, ...rest } = record;
  return rest;
}

/**
 * Present any thrown value as an {@link ApiError}.
 *
 * A failure originating in the browser rather than in the backend still has to
 * render as a sentence. The original is logged locally for development and is
 * never shown to the user (build plan 5.10).
 */
function asApiError(error) {
  if (error instanceof ApiError) return error;
  console.error(error);
  return new ApiError("Something went wrong in the browser. Please try again.");
}
