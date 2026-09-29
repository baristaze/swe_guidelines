import { useEffect, useMemo, useState } from "react";
import { errorMessage } from "../../app/errorMessage";
import { useSlackStatus, useStartSlackInstall, useUninstallSlack } from "../../queries/slack";
import type { Role } from "../../api";
import {
  useApiKeys,
  useCreateApiKey,
  useMe,
  useMemberships,
  useRevokeApiKey,
  useUpdateMemberRole,
  useUsers,
} from "../../queries/tenancy";
import { useNoticesStore } from "../../store/notices";
import { isPlanLimit } from "../../store/upgrade";
import { apiKeyRows, canManageKeys, memberRows } from "./settingsModel";
import { canManageSlack, installOutcomeText, slackSummary } from "./slackModel";

/** The `?slack=` outcome Slack's install sends the browser back with, read
 * once and taken off the address, so a reload does not say it again. */
function takeInstallOutcome(): string | null {
  const url = new URL(window.location.href);
  const outcome = url.searchParams.get("slack");
  if (outcome === null) return null;
  url.searchParams.delete("slack");
  window.history.replaceState(window.history.state, "", url);
  return outcome;
}

export function useSettingsVm() {
  const me = useMe();
  const users = useUsers();
  const memberships = useMemberships();
  const changeRole = useUpdateMemberRole();
  const mayManageKeys = canManageKeys(me.data);
  const apiKeys = useApiKeys(mayManageKeys);
  const createKey = useCreateApiKey();
  const revokeKey = useRevokeApiKey();
  const notify = useNoticesStore((s) => s.notify);
  const [newKeyName, setNewKeyName] = useState("");
  const [issuedKey, setIssuedKey] = useState<string | null>(null);
  const slack = useSlackStatus();
  const startInstall = useStartSlackInstall();
  const uninstall = useUninstallSlack();
  const mayManageSlack = canManageSlack(me.data);

  useEffect(() => {
    const said = installOutcomeText(takeInstallOutcome());
    if (said) notify(said);
  }, [notify]);

  const members = useMemo(
    () => memberRows(users.data ?? [], memberships.data ?? [], me.data),
    [users.data, memberships.data, me.data],
  );
  const keys = useMemo(() => apiKeyRows(apiKeys.data ?? [], new Date()), [apiKeys.data]);

  // A write that fails is said, not swallowed: the notice names the refusal
  // and its request id, and the key list refetches on its own.
  const createApiKey = async () => {
    if (!newKeyName.trim()) return;
    try {
      const issued = await createKey.mutateAsync({ name: newKeyName.trim(), role: "member" });
      // A retried create the server answers from its record carries no
      // secret: the first answer, the only one that had it, was lost. The key
      // exists and nobody can use it, which is said, not shown as success.
      if (issued.key === null) {
        setIssuedKey(null);
        notify(`The key "${issued.api_key.name}" was created, but its secret was lost on the way back; revoke it and create another.`);
      } else {
        setIssuedKey(issued.key);
      }
      setNewKeyName("");
    } catch (caught) {
      // A plan without keys is answered by the upgrade dialog.
      if (!isPlanLimit(caught)) notify(errorMessage(caught, "The key was not created."));
    }
  };

  // Awaited, not handed to `mutate` as per-call callbacks: one hook holds one
  // mutation observer, and a second revoke started before the first answers
  // drops the first call's callbacks, so its refusal would go unsaid.
  const revokeApiKey = async (id: string) => {
    try {
      await revokeKey.mutateAsync(id);
    } catch (caught) {
      notify(errorMessage(caught, "The key was not revoked."));
    }
  };

  // A refusal is said in the server's words: the rules are the server's.
  const setRole = async (userId: string, role: Role) => {
    try {
      await changeRole.mutateAsync({ userId, body: { role } });
    } catch (caught) {
      notify(errorMessage(caught, "The role was not changed."));
    }
  };

  // Off to Slack's page; Slack sends the browser back here, and the outcome
  // is said then. A refusal (not configured, not allowed) is said now.
  const installSlack = async () => {
    try {
      const start = await startInstall.mutateAsync();
      if (!start.url) notify("The install link was lost on the way back; click Add to Slack again.");
    } catch (caught) {
      notify(errorMessage(caught, "Slack's install page could not be opened."));
    }
  };

  const uninstallSlack = async () => {
    try {
      await uninstall.mutateAsync();
    } catch (caught) {
      notify(errorMessage(caught, "Acme was not removed from Slack."));
    }
  };

  return {
    me: me.data,
    loading: me.isPending || users.isPending || (mayManageKeys && apiKeys.isPending),
    error: me.error ?? users.error ?? (mayManageKeys ? apiKeys.error : null),
    members,
    setRole,
    changingRole: changeRole.isPending,
    keys,
    canManageKeys: mayManageKeys,
    // The key list is paged: the screen shows a page and asks for the next,
    // so a key past the first page is still there to revoke.
    hasMoreKeys: apiKeys.hasNextPage,
    loadingMoreKeys: apiKeys.isFetchingNextPage,
    showMoreKeys: () => void apiKeys.fetchNextPage(),
    newKeyName,
    setNewKeyName,
    issuedKey,
    dismissIssuedKey: () => setIssuedKey(null),
    createApiKey,
    creating: createKey.isPending,
    revokeApiKey,
    slack: {
      loading: slack.isPending,
      error: slack.error,
      summary: slackSummary(slack.data),
      installed: Boolean(slack.data?.installation),
      canManage: mayManageSlack,
      install: installSlack,
      installing: startInstall.isPending,
      uninstall: uninstallSlack,
      uninstalling: uninstall.isPending,
    },
  };
}

export type SettingsVm = ReturnType<typeof useSettingsVm>;
