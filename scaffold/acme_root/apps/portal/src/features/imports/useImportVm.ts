import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { storeFetch } from "../../api";
import { errorMessage } from "../../app/errorMessage";
import { mediaCalls } from "../../queries/attachments";
import { useBilling } from "../../queries/billing";
import { importCalls, useRecentImports, useResumeImport } from "../../queries/imports";
import { keys } from "../../queries/keys";
import { notify } from "../../store/notices";
import { useUpgradeStore } from "../../store/upgrade";
import { startImport, type ImportEffects } from "./importFlow";
import { activeTasksLimit, headline, isParkedOnThePlan, isRunning, progressLine, shownImport, skippedLines } from "./importModel";

const effects: ImportEffects = {
  startFile: importCalls.startFile,
  issueUpload: mediaCalls.issueUpload,
  postToStore: (url, form) => storeFetch(url, { method: "POST", body: form }),
  putContent: mediaCalls.putContent,
  confirm: mediaCalls.confirm,
  start: importCalls.start,
};

export function useImportVm(canWrite: boolean) {
  const queryClient = useQueryClient();
  const recent = useRecentImports(canWrite);
  const billing = useBilling();
  const resume = useResumeImport();
  const openUpgrade = useUpgradeStore((s) => s.open);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [picked, setPicked] = useState<File | null>(null);
  const [starting, setStarting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  // An import that ended long before the page opened is not news; one that
  // ends while it is open stays until dismissed.
  const [openedAt] = useState(() => Date.now());

  const shown = useMemo(
    () => shownImport(recent.data?.items ?? [], dismissed, openedAt),
    [recent.data, dismissed, openedAt],
  );
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keys.imports.all });

  const openDialog = () => {
    setPicked(null);
    setFailure(null);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    if (!starting) setDialogOpen(false);
  };

  const start = async () => {
    if (!picked || starting) return;
    setStarting(true);
    setFailure(null);
    try {
      await startImport({ name: picked.name, type: picked.type, size: picked.size, bytes: picked }, effects);
      setDialogOpen(false);
    } catch (caught) {
      setFailure(errorMessage(caught, `Could not import ${picked.name}.`));
    } finally {
      setStarting(false);
      refresh();
    }
  };

  const resumeShown = async () => {
    if (!shown) return;
    try {
      await resume.mutateAsync(shown.id);
    } catch (caught) {
      notify(errorMessage(caught, "Could not resume the import."));
    } finally {
      refresh();
    }
  };

  const upgrade = () => {
    if (billing.data) openUpgrade(activeTasksLimit(billing.data));
  };

  return {
    canImport: canWrite,
    dialogOpen,
    openDialog,
    closeDialog,
    picked,
    pick: (file: File | null) => {
      setPicked(file);
      setFailure(null);
    },
    starting,
    failure,
    start,
    shown: shown
      ? {
          id: shown.id,
          headline: headline(shown),
          progress: progressLine(shown),
          running: isRunning(shown),
          parkedOnThePlan: isParkedOnThePlan(shown),
          ended: shown.finished_at !== null,
          failed: shown.status === "failed",
          skipped: skippedLines(shown),
          created: shown.created,
          total: shown.total,
        }
      : null,
    resuming: resume.isPending,
    resume: resumeShown,
    upgrade,
    canUpgrade: billing.data?.can_manage ?? false,
    dismiss: () => {
      if (shown) setDismissed((now) => new Set([...now, shown.id]));
    },
  };
}

export type ImportVm = ReturnType<typeof useImportVm>;
