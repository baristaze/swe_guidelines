import type { MeView, Role } from "../../api";
import { useMemo, useState } from "react";
import { errorMessage } from "../../app/errorMessage";
import {
  useInvitations,
  useInviteMember,
  useResendInvitation,
  useRevokeInvitation,
  useSsoLink,
} from "../../queries/tenancy";
import { useNoticesStore } from "../../store/notices";
import { canManageMembers, checkInvite, invitableRoles, invitationRows, ssoAvailable } from "./settingsModel";

/** Inviting people and the org's single sign-on, for a member who manages
 * members. The identity provider sends the invitation's email and hosts the
 * page where an admin connects their own identity provider. */
export function useInvitationsVm(me: MeView | undefined) {
  const mayManage = canManageMembers(me);
  const invitations = useInvitations(mayManage);
  const invite = useInviteMember();
  const resend = useResendInvitation();
  const revoke = useRevokeInvitation();
  const ssoLink = useSsoLink();
  const notify = useNoticesStore((s) => s.notify);
  const roles = invitableRoles(me);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [error, setError] = useState<string | null>(null);

  const rows = useMemo(() => invitationRows(invitations.data ?? [], new Date()), [invitations.data]);

  const send = async () => {
    setError(null);
    const refused = checkInvite(email);
    if (refused) {
      setError(refused);
      return;
    }
    try {
      await invite.mutateAsync({ email: email.trim(), role: roles.includes(role) ? role : "member" });
      setEmail("");
    } catch (caught) {
      notify(errorMessage(caught, "The invitation was not sent."));
    }
  };

  const resendOne = async (id: string) => {
    try {
      await resend.mutateAsync(id);
    } catch (caught) {
      notify(errorMessage(caught, "The invitation was not sent again."));
    }
  };

  const revokeOne = async (id: string) => {
    try {
      await revoke.mutateAsync(id);
    } catch (caught) {
      notify(errorMessage(caught, "The invitation was not revoked."));
    }
  };

  // The link is short-lived, so it is asked for on the click and followed at once.
  const openSso = async (intent: "sso" | "domain_verification") => {
    try {
      const link = await ssoLink.mutateAsync({ intent, return_url: `${window.location.origin}/settings` });
      window.location.assign(link.url);
    } catch (caught) {
      notify(errorMessage(caught, "Single sign-on could not be opened."));
    }
  };

  return {
    mayManage,
    sso: ssoAvailable(me),
    roles,
    email,
    role,
    error,
    rows,
    loading: mayManage && invitations.isPending,
    sending: invite.isPending,
    hasMore: invitations.hasNextPage,
    loadingMore: invitations.isFetchingNextPage,
    showMore: () => void invitations.fetchNextPage(),
    setEmail,
    setRole: (value: string) => setRole(value as Role),
    send,
    resend: resendOne,
    revoke: revokeOne,
    openSso,
    openingSso: ssoLink.isPending,
  };
}
