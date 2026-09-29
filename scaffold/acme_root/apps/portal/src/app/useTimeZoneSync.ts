// Records the person's time zone once per session, after sign-in. Quiet by
// design: a refusal or a network failure is dropped, nothing is shown, and
// the page never waits on it. The next session tries again.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { IdentityView, UpdateIdentityRequest } from "../api";
import { useIdentity } from "../queries/tenancy";
import { keys } from "../queries/keys";
import { api } from "./api";
import { browserTimeZone, zoneToSend } from "./timeZone";

export function useTimeZoneSync(browser: () => string | null = browserTimeZone) {
  const identity = useIdentity();
  const queryClient = useQueryClient();
  const sent = useRef(false);
  const stored = identity.data?.time_zone;
  const known = identity.isSuccess;
  useEffect(() => {
    if (!known || sent.current) return;
    const zone = zoneToSend(browser(), stored);
    if (!zone) return;
    sent.current = true;
    const body: UpdateIdentityRequest = { time_zone: zone };
    api
      .patch<IdentityView>("/v1/me/identity", body)
      .then((updated) => queryClient.setQueryData(keys.identity, updated))
      .catch(() => undefined);
  }, [known, stored, browser, queryClient]);
}

/** The hook as a component, for the signed-in shell. */
export function TimeZoneSync() {
  useTimeZoneSync();
  return null;
}
