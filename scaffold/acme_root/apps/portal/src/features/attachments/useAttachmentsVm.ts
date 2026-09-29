import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { storeFetch, type FileView } from "../../api";
import { errorMessage } from "../../app/errorMessage";
import { mediaCalls, useAttachments, useRemoveAttachment } from "../../queries/attachments";
import { keys } from "../../queries/keys";
import { notify } from "../../store/notices";
import { attachmentRow, humanSize } from "./attachmentsModel";
import {
  downloadAttachment,
  saveBlob,
  uploadAttachment,
  type DownloadEffects,
  type Picked,
  type UploadEffects,
} from "./transfer";

const uploadEffects: UploadEffects = {
  start: mediaCalls.start,
  issueUpload: mediaCalls.issueUpload,
  postToStore: (url, form) => storeFetch(url, { method: "POST", body: form }),
  putContent: mediaCalls.putContent,
  confirm: mediaCalls.confirm,
};

const downloadEffects: DownloadEffects = {
  issueDownload: mediaCalls.issueDownload,
  fetchFromStore: (url) => storeFetch(url),
  getContent: mediaCalls.getContent,
  save: saveBlob,
};

export interface Uploading {
  key: string;
  name: string;
  size: string;
}

export function useAttachmentsVm(taskId: string) {
  const queryClient = useQueryClient();
  const attachments = useAttachments(taskId);
  const remove = useRemoveAttachment();
  const [uploading, setUploading] = useState<Uploading[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const files = useMemo(() => attachments.data?.items ?? [], [attachments.data]);
  const rows = useMemo(() => files.map(attachmentRow), [files]);
  // The list and the usage both carry the file; a push from another window
  // refreshes them the same way.
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keys.files.all });

  const upload = async (picked: readonly File[]) => {
    await Promise.all(
      picked.map(async (file) => {
        const key = crypto.randomUUID();
        setUploading((now) => [...now, { key, name: file.name, size: humanSize(file.size) }]);
        const item: Picked = { name: file.name, type: file.type, size: file.size, bytes: file };
        try {
          await uploadAttachment(taskId, item, uploadEffects);
        } catch (caught) {
          notify(errorMessage(caught, `Could not attach ${file.name}.`));
        } finally {
          setUploading((now) => now.filter((u) => u.key !== key));
          refresh();
        }
      }),
    );
  };

  const byId = (id: string): FileView | undefined => files.find((f) => f.id === id);

  const download = async (id: string) => {
    const file = byId(id);
    if (!file) return;
    setBusyId(id);
    try {
      await downloadAttachment(file, downloadEffects);
    } catch (caught) {
      notify(errorMessage(caught, `Could not download ${file.name}.`));
    } finally {
      setBusyId(null);
    }
  };

  const destroy = async (id: string) => {
    setBusyId(id);
    try {
      await remove.mutateAsync({ taskId, fileId: id });
    } catch (caught) {
      notify(errorMessage(caught, "Could not remove the file."));
    } finally {
      setBusyId(null);
      refresh();
    }
  };

  return {
    loading: attachments.isLoading,
    rows,
    hasMore: attachments.data?.next_cursor != null,
    uploading,
    busyId,
    upload,
    download,
    destroy,
  };
}

export type AttachmentsVm = ReturnType<typeof useAttachmentsVm>;
