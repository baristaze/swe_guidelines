import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type {
  AccountDeletedView,
  AddApiKeyRequest,
  ApiKeyPageView,
  ApiKeyView,
  CreateTeamOrgRequest,
  DeleteAccountRequest,
  DeleteOrgRequest,
  DevSignInRequest,
  ExchangeSessionRequest,
  IdentityView,
  IssuedApiKeyView,
  IssuedLoginView,
  IssuedSessionView,
  InvitationPageView,
  InvitationView,
  InviteMemberRequest,
  LogoutRequest,
  MembershipChoicePageView,
  MembershipChoiceView,
  MembershipPageView,
  MembershipView,
  MeView,
  OrgDeletedView,
  SignedOutView,
  SignInCallbackRequest,
  SignInStartRequest,
  SignInStartView,
  SsoLinkRequest,
  SsoLinkView,
  UpdateMembershipRequest,
  UserPageView,
  UserView,
} from "../api";
import { api } from "../app/api";
import { keys } from "./keys";

/** Both lists are paged by the server's cursor; these are page sizes, not
 * ceilings, so nothing is hidden past the end of the first page. The member
 * list is read whole (below), so it asks for the largest page the server
 * gives; the key list is read a page at a time on the screen that shows it. */
const USERS_PAGE_SIZE = 200;
export const API_KEYS_PAGE_SIZE = 50;
export const INVITATIONS_PAGE_SIZE = 50;
/** A person belongs to at most a hundred orgs; the chip reads them whole. */
const MY_MEMBERSHIPS_PAGE_SIZE = 200;

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: ({ signal }) => api.get<MeView>("/v1/me", { signal }) });
}

/** The person behind the session, across every org: the time zone lives here. */
export function useIdentity() {
  return useQuery({
    queryKey: keys.identity,
    queryFn: ({ signal }) => api.get<IdentityView>("/v1/me/identity", { signal }),
  });
}

/** Every member of the org, page after page. The list is not a screen of its
 * own: it names the people on tasks and fills the assignee picker, and a
 * member missing from it reads as a former member and cannot be assigned, so this
 * follows the cursor to the end rather than stopping at one page. The pages
 * loaded so far are returned as they arrive. */
export function useUsers() {
  const query = useInfiniteQuery({
    queryKey: keys.users.list(USERS_PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last: UserPageView) => last.next_cursor,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<UserPageView>(`/v1/users?limit=${USERS_PAGE_SIZE}${cursor}`, { signal });
    },
  });
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query;
  // A page that failed is not asked for again on its own: retries are the
  // client's, and re-firing here on every render would be a loop against a
  // server that just refused. The pages in hand are what the app shows, and
  // the next invalidation starts the walk over.
  const walking = hasNextPage && !isFetchNextPageError;
  useEffect(() => {
    if (walking && !isFetchingNextPage) void fetchNextPage();
  }, [walking, isFetchingNextPage, fetchNextPage]);
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.items) as UserView[] | undefined,
    /** Pending until the whole list is in hand: a caller that maps ids to
     * names would otherwise call a member still on the way a former member. */
    isPending: query.isPending || walking,
  };
}

/** Every membership of the org, page after page, as the member list is read:
 * the role beside each member in Settings. Read whole, like the users, so a
 * member on a later page still shows their role. */
export function useMemberships() {
  const query = useInfiniteQuery({
    queryKey: keys.memberships.list(USERS_PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last: MembershipPageView) => last.next_cursor,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<MembershipPageView>(`/v1/memberships?limit=${USERS_PAGE_SIZE}${cursor}`, { signal });
    },
  });
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query;
  const walking = hasNextPage && !isFetchNextPageError;
  useEffect(() => {
    if (walking && !isFetchingNextPage) void fetchNextPage();
  }, [walking, isFetchingNextPage, fetchNextPage]);
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.items) as MembershipView[] | undefined,
    isPending: query.isPending || walking,
  };
}

/** Gives another member a role. The server holds the rules: never above the
 * caller's own role, never for the caller themselves, and never for the
 * person of a personal org. The push refreshes every open Settings too. */
export function useUpdateMemberRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: UpdateMembershipRequest }) =>
      api.patch<MembershipView>(`/v1/memberships/${userId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.memberships.all }),
  });
}

/** Only a member who may manage keys asks for them: `GET /v1/api-keys`
 * refuses anyone else, and a refusal nobody can act on is not an error to
 * show. Disabled, the query stays pending and never fetches, so a caller
 * reads `enabled` and not `isPending` to know whether to wait. The list is
 * paged on demand: the screen shows the first page and asks for the next. */
export function useApiKeys(enabled = true) {
  const query = useInfiniteQuery({
    queryKey: keys.apiKeys.list(API_KEYS_PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last: ApiKeyPageView) => last.next_cursor,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<ApiKeyPageView>(`/v1/api-keys?limit=${API_KEYS_PAGE_SIZE}${cursor}`, {
        signal,
      });
    },
    enabled,
  });
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.items) as ApiKeyView[] | undefined,
  };
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AddApiKeyRequest) =>
      api.post<IssuedApiKeyView>("/v1/api-keys", body, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apiKeys.all }),
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (apiKeyId: string) => api.del<ApiKeyView>(`/v1/api-keys/${apiKeyId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apiKeys.all }),
  });
}

/** The orgs the signed-in person belongs to, page after page, read with the
 * session this tab holds. The chip needs the whole list to offer a switch. */
export function useMyMemberships() {
  const query = useInfiniteQuery({
    queryKey: keys.myMemberships.list(MY_MEMBERSHIPS_PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last: MembershipChoicePageView) => last.next_cursor,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<MembershipChoicePageView>(
        `/v1/auth/memberships?limit=${MY_MEMBERSHIPS_PAGE_SIZE}${cursor}`,
        { signal },
      );
    },
  });
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query;
  const walking = hasNextPage && !isFetchNextPageError;
  useEffect(() => {
    if (walking && !isFetchingNextPage) void fetchNextPage();
  }, [walking, isFetchingNextPage, fetchNextPage]);
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.items) as MembershipChoiceView[] | undefined,
    isPending: query.isPending || walking,
  };
}

/** Where the browser goes to sign in at the identity provider. Sent once,
 * with no bearer: nothing is signed in yet. */
export function useStartSignIn() {
  return useMutation({
    mutationFn: (body: SignInStartRequest) =>
      api.post<SignInStartView>("/v1/auth/sign-in", body, { token: null }),
  });
}

/** The code the browser brought back, exchanged by the API. A code is good
 * once, so this is sent once; the answer is a sign-in, and a person nobody
 * knew is signed up by it, with their personal org. */
export function useFinishSignIn() {
  return useMutation({
    mutationFn: (body: SignInCallbackRequest) =>
      api.post<IssuedLoginView>("/v1/auth/callback", body, { token: null }),
  });
}

/** The local stack's sign-in by address alone; a deployed API answers 404. */
export function useDevSignIn() {
  return useMutation({
    mutationFn: (body: DevSignInRequest) =>
      api.post<IssuedLoginView>("/v1/auth/dev-sign-in", body, { token: null }),
  });
}

/** Revokes the session the token names. The server answers with the session,
 * revoked, and where the browser goes to end the identity provider's session
 * behind it, which sends the browser back to `return_to`. */
export function useLogout() {
  return useMutation({
    mutationFn: (body: LogoutRequest) => api.post<SignedOutView>("/v1/auth/logout", body),
  });
}

/** Ends a session this tab no longer holds, with that session's own token: a
 * sign-in over a tab that was signed in ends the session it replaced. It
 * sends no `return_to`, and its answer's provider logout is not followed. */
export function useEndReplacedSession() {
  return useMutation({
    mutationFn: (token: string) => api.post<SignedOutView>("/v1/auth/logout", undefined, { token }),
  });
}

/** Deletes the caller's whole account. The server answers once it is gone,
 * with where the browser goes to end the identity provider's session, as a
 * sign-out does. Sent once: a retry would meet a session that is gone. */
export function useDeleteAccount() {
  return useMutation({
    mutationFn: (body: DeleteAccountRequest) => api.post<AccountDeletedView>("/v1/me/deletion", body),
  });
}

/** Deletes the team org this tab is in, for everyone in it. The owner types
 * its name; the answer carries the owner's session in their personal org.
 * Sent once: a retry would meet a session that is gone. */
export function useDeleteOrg() {
  return useMutation({
    mutationFn: (body: DeleteOrgRequest) => api.post<OrgDeletedView>("/v1/orgs/current/deletion", body),
  });
}

export function useExchangeSession() {
  return useMutation({
    mutationFn: ({ loginToken, body }: { loginToken: string; body: ExchangeSessionRequest }) =>
      api.post<IssuedSessionView>("/v1/auth/sessions", body, { token: loginToken }),
  });
}

/** The switch: the exchange presented with the session this tab holds. The
 * server ends that session in the same write and answers with the new one. */
export function useSwitchOrg() {
  return useMutation({
    mutationFn: (orgId: string) => api.post<IssuedSessionView>("/v1/auth/sessions", { org_id: orgId }),
  });
}

/** A team org the signed-in person makes and owns, from the tenant they are
 * in. A creating POST, so it carries an idempotency key and a retry makes
 * one org. The answer is the person's place in it, which the switch takes;
 * the list of places is read again, since no push names it. */
export function useCreateOrg() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateTeamOrgRequest) =>
      api.post<MembershipChoiceView>("/v1/orgs", body, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.myMemberships.all }),
  });
}

/** The org's pending invitations, for a member who manages members; paged on
 * demand like the key list. Disabled for anyone else, who would be refused. */
export function useInvitations(enabled = true) {
  const query = useInfiniteQuery({
    queryKey: keys.invitations.list(INVITATIONS_PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last: InvitationPageView) => last.next_cursor,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<InvitationPageView>(`/v1/invitations?limit=${INVITATIONS_PAGE_SIZE}${cursor}`, {
        signal,
      });
    },
    enabled,
  });
  return {
    ...query,
    data: query.data?.pages.flatMap((page) => page.items) as InvitationView[] | undefined,
  };
}

/** The identity provider sends the email with the link. A creating POST, so
 * it carries an idempotency key and a retry sends one invitation. */
export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InviteMemberRequest) =>
      api.post<InvitationView>("/v1/invitations", body, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.invitations.all }),
  });
}

export function useResendInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => api.post<InvitationView>(`/v1/invitations/${invitationId}/resend`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.invitations.all }),
  });
}

export function useRevokeInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => api.del<InvitationView>(`/v1/invitations/${invitationId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.invitations.all }),
  });
}

/** A short-lived link to the identity provider's admin portal for this org. */
export function useSsoLink() {
  return useMutation({
    mutationFn: (body: SsoLinkRequest) => api.post<SsoLinkView>("/v1/orgs/current/sso-link", body),
  });
}
