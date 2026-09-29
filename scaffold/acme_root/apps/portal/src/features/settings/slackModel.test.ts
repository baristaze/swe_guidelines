import type { MeView, SlackInstallationView, SlackStatusView } from "../../api";
import { describe, expect, it } from "vitest";
import { brokenReasonText, canManageSlack, installOutcomeText, slackSummary } from "./slackModel";

const installation = (overrides: Partial<SlackInstallationView> = {}): SlackInstallationView => ({
  id: "i1",
  team_id: "T1",
  team_name: "Ajax",
  channel_id: "C0123",
  status: "ok",
  broken_reason: null,
  created_by: "u1",
  created_at: "2026-09-22T10:00:00Z",
  updated_at: "2026-09-22T10:00:00Z",
  ...overrides,
});

const status = (i: SlackInstallationView | null): SlackStatusView => ({ installation: i });

const me = (permissions: MeView["permissions"]): MeView => ({
  user: { id: "u1", email: "a@b.c", display_name: "Ann", created_at: "2026-09-01T00:00:00Z" },
  org: { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: "2026-09-01T00:00:00Z" },
  role: "member",
  permissions,
  app: "portal",
});

describe("slack model", () => {
  it("lets an owner or an admin install and remove, and a member only look", () => {
    expect(canManageSlack(me(["read", "write", "manage_members"]))).toBe(true);
    expect(canManageSlack(me(["read", "write"]))).toBe(false);
    expect(canManageSlack(undefined)).toBe(false);
  });

  it("says the state of the installation and what to do next", () => {
    expect(slackSummary(undefined)).toEqual({
      state: "not_installed",
      line: "Not installed.",
      fix: null,
      installLabel: "Add to Slack",
    });
    expect(slackSummary(status(installation({ channel_id: null })))).toEqual({
      state: "no_channel",
      line: "Installed in Ajax. No channel gets posts yet.",
      fix: "In the channel for reminders and task updates, type /invite @acme, then /acme connect.",
      installLabel: "Add to Slack again",
    });
    expect(slackSummary(status(installation()))).toEqual({
      state: "connected",
      line: "Installed in Ajax, posting to channel C0123.",
      fix: null,
      installLabel: "Add to Slack again",
    });
    expect(slackSummary(status(installation({ status: "broken", broken_reason: "not_in_channel" })))).toEqual({
      state: "broken",
      line: "Installed in Ajax, but posting there fails.",
      fix: "@acme is not in the channel: type /invite @acme in it, and posting resumes.",
      installLabel: "Add to Slack again",
    });
  });

  it("puts each refusal in plain words", () => {
    expect(brokenReasonText("channel_not_found")).toBe(
      "The channel was deleted, or @acme was removed from it: type /invite @acme in it, and posting resumes; or type /acme connect in another.",
    );
    expect(brokenReasonText("is_archived")).toBe("The channel was archived: type /acme connect in another.");
    expect(brokenReasonText("invalid_refresh_token")).toBe(
      "Slack no longer accepts the install's token: add Acme to Slack again.",
    );
    expect(brokenReasonText("token_missing")).toBe(brokenReasonText("token_revoked"));
    expect(brokenReasonText(null)).toBe(
      "Slack refuses posts to the channel: type /acme connect in it again, or in another.",
    );
  });

  it("says how an install went when Slack sends the browser back", () => {
    expect(installOutcomeText(null)).toBeNull();
    expect(installOutcomeText("installed")).toBe(
      "Acme is in Slack. Type /acme connect in the channel it should post to.",
    );
    expect(installOutcomeText("taken")).toContain("another Acme org");
    expect(installOutcomeText("expired")).toContain("expired or was already used");
    expect(installOutcomeText("cancelled")).toContain("cancelled");
    expect(installOutcomeText("failed")).toBe("Slack refused the install. Try Add to Slack again.");
  });
});
