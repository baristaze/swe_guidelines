import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SlackInstallStartView, SlackStatusView } from "../api";
import { api } from "../app/api";
import type { Go } from "./billing";
import { keys } from "./keys";

const leave: Go = (url) => window.location.assign(url);

/** Whether the org installed Acme in Slack, and where it posts. Any member reads it. */
export function useSlackStatus() {
  return useQuery({
    queryKey: keys.slack.installation,
    queryFn: ({ signal }) => api.get<SlackStatusView>("/v1/slack/installation", { signal }),
  });
}

/** Slack's install page for the org's workspace, under an idempotency key,
 * then off to it. Slack sends the browser back to the settings page, which
 * says how it went. A replay's answer carries no link: the one-time state in
 * it went to the first answer alone. */
export function useStartSlackInstall(go: Go = leave) {
  return useMutation({
    mutationFn: () =>
      api.post<SlackInstallStartView>("/v1/slack/installation", undefined, {
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: (start) => {
      if (start.url) go(start.url);
    },
  });
}

/** Removes the app from the org's workspace; the answer is the status after. */
export function useUninstallSlack() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.del<SlackStatusView>("/v1/slack/installation"),
    onSuccess: (status) => queryClient.setQueryData(keys.slack.installation, status),
  });
}
