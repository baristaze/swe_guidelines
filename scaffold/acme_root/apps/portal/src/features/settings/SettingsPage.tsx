import { Banner, Button, Card, ErrorText, LinkButton, Muted, Page, Select, Table, TextField } from "../../design/kit";
import { AppNav } from "../../app/AppNav";
import { tokens } from "../../design/tokens";
import { DeleteAccountCard } from "./DeleteAccountCard";
import { DeleteOrgCard } from "./DeleteOrgCard";
import { RoleControl } from "./RoleControl";
import { StorageCard } from "./StorageCard";
import { useDeleteAccountVm } from "./useDeleteAccountVm";
import { useDeleteOrgVm } from "./useDeleteOrgVm";
import { useInvitationsVm } from "./useInvitationsVm";
import { useSettingsVm } from "./useSettingsVm";

export function SettingsPage() {
  const vm = useSettingsVm();
  const invites = useInvitationsVm(vm.me);
  const leaving = useDeleteAccountVm(vm.me);
  const closing = useDeleteOrgVm(vm.me);
  return (
    <Page title="Settings" nav={<AppNav />}>
      {vm.error ? <Banner>{vm.error.message}</Banner> : null}
      <Card title="Members" id="members">
        {vm.loading ? (
          <Muted>Loading</Muted>
        ) : (
          <Table
            headers={["Name", "Email", "Role", "Joined"]}
            rows={vm.members.map((m) => [
              m.name,
              m.email,
              <RoleControl key={m.id} row={m} onChange={(id, role) => void vm.setRole(id, role)} busy={vm.changingRole} />,
              m.joined,
            ])}
          />
        )}
        {invites.mayManage ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void invites.send();
            }}
            style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.sm, alignItems: "end", marginTop: tokens.space.md }}
          >
            <TextField label="Invite by email" type="email" value={invites.email} onChange={invites.setEmail} />
            <Select
              label="Role"
              value={invites.role}
              options={invites.roles.map((role) => ({ value: role, label: role }))}
              onChange={invites.setRole}
            />
            <Button type="submit" disabled={invites.sending}>
              {invites.sending ? "Sending…" : "Invite"}
            </Button>
          </form>
        ) : null}
        {invites.error ? <ErrorText>{invites.error}</ErrorText> : null}
      </Card>
      <StorageCard />
      {invites.mayManage ? (
        <Card title="Pending invitations">
          {invites.loading ? (
            <Muted>Loading</Muted>
          ) : invites.rows.length === 0 ? (
            <Muted>No invitation is waiting.</Muted>
          ) : (
            <Table
              headers={["Email", "Role", "Expires", ""]}
              rows={invites.rows.map((row) => [
                row.email,
                row.role,
                row.expired ? `${row.expires} (expired)` : row.expires,
                <span key={row.id} style={{ display: "inline-flex", gap: tokens.space.sm }}>
                  <Button tone="plain" onClick={() => void invites.resend(row.id)}>
                    Resend
                  </Button>
                  <Button tone="danger" onClick={() => void invites.revoke(row.id)}>
                    Revoke
                  </Button>
                </span>,
              ])}
            />
          )}
          {invites.hasMore ? (
            <div style={{ paddingTop: tokens.space.md }}>
              {invites.loadingMore ? <Muted>Loading</Muted> : <LinkButton onClick={invites.showMore}>Show more</LinkButton>}
            </div>
          ) : null}
        </Card>
      ) : null}
      {invites.sso ? (
        <Card title="Single sign-on">
          <Muted>
            Your organization&apos;s admin connects its identity provider (Okta, Entra ID, Google Workspace, any SAML or
            OIDC one) on WorkOS&apos;s page, and people of your verified domain then sign in through it.
          </Muted>
          <div style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.sm, marginTop: tokens.space.md }}>
            <Button onClick={() => void invites.openSso("sso")} disabled={invites.openingSso}>
              Set up single sign-on
            </Button>
            <Button tone="plain" onClick={() => void invites.openSso("domain_verification")} disabled={invites.openingSso}>
              Verify a domain
            </Button>
          </div>
        </Card>
      ) : null}
      {vm.canManageKeys ? (
        <Card title="API keys">
          {vm.issuedKey ? (
            <Banner>
              Copy this key now; it is shown once: <code>{vm.issuedKey}</code>{" "}
              <Button tone="plain" onClick={vm.dismissIssuedKey}>
                Done
              </Button>
            </Banner>
          ) : null}
          <div style={{ display: "flex", gap: 8, alignItems: "end", margin: "16px 0" }}>
            <TextField label="New key name" value={vm.newKeyName} onChange={vm.setNewKeyName} />
            <Button onClick={() => void vm.createApiKey()} disabled={vm.creating}>
              Create key
            </Button>
          </div>
          <Table
            headers={["Name", "Role", "State", "Expires", ""]}
            rows={vm.keys.map((k) => [
              k.name,
              k.role,
              k.state,
              k.expires,
              k.state === "active" ? (
                <Button tone="danger" onClick={() => void vm.revokeApiKey(k.id)}>
                  Revoke
                </Button>
              ) : (
                ""
              ),
            ])}
          />
          {vm.hasMoreKeys ? (
            <div style={{ paddingTop: tokens.space.md }}>
              {vm.loadingMoreKeys ? (
                <Muted>Loading</Muted>
              ) : (
                <LinkButton onClick={vm.showMoreKeys}>Show more</LinkButton>
              )}
            </div>
          ) : null}
        </Card>
      ) : null}
      <DeleteOrgCard vm={closing} />
      <DeleteAccountCard vm={leaving} />
    </Page>
  );
}
