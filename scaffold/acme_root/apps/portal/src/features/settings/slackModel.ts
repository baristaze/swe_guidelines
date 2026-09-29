// Pure: what the Slack section of the settings screen says, and who may act
// on it. The org installs Acme into one Slack workspace, from here; the
// channel it posts to is chosen in Slack, with `/acme connect`.
import type { MeView, SlackStatusView } from "../../api";

export const INVITE_COMMAND = "/invite @acme";
export const CONNECT_COMMAND = "/acme connect";

export type SlackState = "not_installed" | "no_channel" | "connected" | "broken";

export interface SlackSummary {
  state: SlackState;
  /** One line on the installation. */
  line: string;
  /** What to do next, in plain words; null when nothing is to be done. */
  fix: string | null;
  /** The label of the button that starts an install. */
  installLabel: string;
}

/** Installing and removing take an owner or an admin: the API refuses anyone else. */
export function canManageSlack(me: MeView | undefined): boolean {
  return me?.permissions.includes("manage_members") ?? false;
}

const TOKEN_REFUSALS = new Set([
  "invalid_auth",
  "not_authed",
  "token_revoked",
  "account_inactive",
  "invalid_refresh_token",
  "team_not_found",
  "token_missing",
]);

export function brokenReasonText(reason: string | null): string {
  if (reason && TOKEN_REFUSALS.has(reason)) {
    return "Slack no longer accepts the install's token: add Acme to Slack again.";
  }
  switch (reason) {
    // Slack tells Acme when @acme joins the channel again, and posting
    // resumes by itself: no /acme connect, no new install.
    case "not_in_channel":
      return `@acme is not in the channel: type ${INVITE_COMMAND} in it, and posting resumes.`;
    case "channel_not_found":
      return `The channel was deleted, or @acme was removed from it: type ${INVITE_COMMAND} in it, and posting resumes; or type ${CONNECT_COMMAND} in another.`;
    case "is_archived":
      return `The channel was archived: type ${CONNECT_COMMAND} in another.`;
    default:
      return reason
        ? `Slack refuses posts to the channel (${reason}): type ${CONNECT_COMMAND} in it again, or in another.`
        : `Slack refuses posts to the channel: type ${CONNECT_COMMAND} in it again, or in another.`;
  }
}

export function slackSummary(status: SlackStatusView | undefined): SlackSummary {
  const installation = status?.installation ?? null;
  if (!installation) {
    return { state: "not_installed", line: "Not installed.", fix: null, installLabel: "Add to Slack" };
  }
  const where = `Installed in ${installation.team_name || installation.team_id}`;
  if (installation.status === "broken") {
    return {
      state: "broken",
      line: `${where}, but posting there fails.`,
      fix: brokenReasonText(installation.broken_reason),
      installLabel: "Add to Slack again",
    };
  }
  if (!installation.channel_id) {
    return {
      state: "no_channel",
      line: `${where}. No channel gets posts yet.`,
      fix: `In the channel for reminders and task updates, type ${INVITE_COMMAND}, then ${CONNECT_COMMAND}.`,
      installLabel: "Add to Slack again",
    };
  }
  return {
    state: "connected",
    line: `${where}, posting to channel ${installation.channel_id}.`,
    fix: null,
    installLabel: "Add to Slack again",
  };
}

/** What the page says when Slack sends the browser back, from `?slack=`. */
export function installOutcomeText(outcome: string | null): string | null {
  switch (outcome) {
    case null:
      return null;
    case "installed":
      return `Acme is in Slack. Type ${CONNECT_COMMAND} in the channel it should post to.`;
    case "cancelled":
      return "Slack was not installed: the install was cancelled on Slack's page.";
    case "taken":
      return "That Slack workspace is installed for another Acme org. Remove it there first.";
    case "expired":
      return "The install link expired or was already used. Click Add to Slack again.";
    default:
      return "Slack refused the install. Try Add to Slack again.";
  }
}
