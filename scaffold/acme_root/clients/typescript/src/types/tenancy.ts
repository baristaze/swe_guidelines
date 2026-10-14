// The tenancy namespace's types: who a caller is and the org it acts in.
// An app names each as tenancy.<Name>, through the package's entry.
import type { components } from "../schema/tenancy";

type Schemas = components["schemas"];

export type OrgView = Schemas["OrgView"];
export type OrgKind = Schemas["OrgKind"];
export type CreateTeamOrgRequest = Schemas["CreateTeamOrgRequest"];
export type UserView = Schemas["UserView"];
export type UserPageView = Schemas["UserPageView"];
export type MeView = Schemas["MeView"];
export type IdentityView = Schemas["IdentityView"];
export type UpdateIdentityRequest = Schemas["UpdateIdentityRequest"];
export type MembershipView = Schemas["MembershipView"];
export type MembershipPageView = Schemas["MembershipPageView"];
export type UpdateMembershipRequest = Schemas["UpdateMembershipRequest"];
export type MembershipChoiceView = Schemas["MembershipChoiceView"];
export type MembershipChoicePageView = Schemas["MembershipChoicePageView"];
export type SignInStartRequest = Schemas["SignInStartRequest"];
export type SignInStartView = Schemas["SignInStartView"];
export type SignInCallbackRequest = Schemas["SignInCallbackRequest"];
export type DevSignInRequest = Schemas["DevSignInRequest"];
export type IssuedLoginView = Schemas["IssuedLoginView"];
export type ExchangeAuthSessionRequest = Schemas["ExchangeAuthSessionRequest"];
export type IssuedAuthSessionView = Schemas["IssuedAuthSessionView"];
export type AuthSessionView = Schemas["AuthSessionView"];
export type LogoutRequest = Schemas["LogoutRequest"];
export type SignedOutView = Schemas["SignedOutView"];
export type DeleteAccountRequest = Schemas["DeleteAccountRequest"];
export type AccountDeletedView = Schemas["AccountDeletedView"];
export type DeleteOrgRequest = Schemas["DeleteOrgRequest"];
export type OrgDeletedView = Schemas["OrgDeletedView"];
export type ApiKeyView = Schemas["ApiKeyView"];
export type ApiKeyPageView = Schemas["ApiKeyPageView"];
export type AddApiKeyRequest = Schemas["AddApiKeyRequest"];
export type IssuedApiKeyView = Schemas["IssuedApiKeyView"];
export type FlagsView = Schemas["FlagsView"];
export type InvitationView = Schemas["InvitationView"];
export type InvitationPageView = Schemas["InvitationPageView"];
export type InviteMemberRequest = Schemas["InviteMemberRequest"];
export type SsoLinkRequest = Schemas["SsoLinkRequest"];
export type SsoLinkView = Schemas["SsoLinkView"];
