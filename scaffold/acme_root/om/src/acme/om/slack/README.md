# Slack

The Slack app an org installs, the channel it posts to, and the record of
what Acme posted there. This is one of the kinds of thing
[Acme is made of](../../../../README.md).

## The nouns

- **Installation**: Acme installed in one Slack workspace, for one org:
  the workspace, the bot user Acme acts as there, the permissions the
  workspace granted, who installed it, and the channel it posts to. An org
  has at most one, and a workspace belongs to at most one org.
- **Bot token**: what Acme calls Slack with, for that workspace alone.
  It is the org's own secret, kept in the secret store under the org and
  never on the installation, which holds only its name. It lasts twelve
  hours, and Acme renews it before then.
- **Install state**: the one-time value an install carries to Slack and
  back. It names the org and the member who clicked **Add to Slack**.
  Acme keeps only its fingerprint. It works once and stops working after
  ten minutes.
- **Post**: one message Acme put in the channel, named by the job that
  posted it, so the same job never posts twice.

## What can happen

- **Install.** An owner or an admin clicks **Add to Slack** in Settings,
  and approves on Slack's own page. Slack sends the browser back, and the
  settings page says how it went. Installing the same workspace again
  keeps the channel; installing another workspace moves the org to it.
- **Bind a channel.** An owner or an admin types `/acme connect` in a
  channel, after `/invite @acme`. Acme posts a first line there, and
  from then on reminders and task updates go to it.
- **Post.** A reminder, a task created, and a task completed are each
  posted in the bound channel.
- **Ask in Slack.** `/acme` shows the person who typed it, and nobody
  else, their ten newest open tasks: the ones My tasks shows in Acme,
  assigned to them, or unassigned and made by them.
  `/acme team` shows the org's ten newest open tasks. Each task shows
  its due date when it has one. When there are more, each says how many
  and links to Acme. Both read and change nothing.
- **Add a task from Slack.** `/acme add <title>` creates a task in the
  org, made by the person who typed it.
- **Mention.** `@acme` answers with the usage, in the thread.
- **Break.** When Slack refuses the channel for good (it is gone,
  archived, or Acme was removed from it), or refuses to renew the token,
  the installation says so and posting stops until someone binds a
  channel again or installs again.
- **Mend.** When someone invites Acme back to the channel it posts to
  (`/invite @acme`), Slack says so, and an installation that channel
  broke is well again: posting resumes, with no `/acme connect` and no
  new install. A token Slack refused still takes a new install.
- **Uninstall.** An owner or an admin clicks **Remove from Slack**; or
  someone removes the app in Slack, and Slack tells Acme. Either way the
  token is deleted and the installation ends.
- **Sweep.** Ended installations, spent states, and the record of posts
  are erased after the retention, thirty days by default.

## The rules

- **A workspace speaks for one org.** A workspace another org holds
  cannot be installed; that org removes Acme first.
- **A command is the typer's.** Acme knows a person by the email on
  their Slack profile: the member of the org whose sign-in holds that
  address. The command runs with that member's role. Someone Acme does
  not know is told to ask an owner or an admin for an invitation.
- **A state works once.** Spending it is one conditional write, so a
  link used twice installs once.
- **One renewal at a time.** A token is renewed by one caller, and the
  others use the token in hand, which still works.
- **A list from Slack is short.** It reads one page of ten and, only when
  more follow, a count; never the whole list.
- **Only an owner or an admin installs, removes, or binds.** Every
  member sees the installation.
- **The token never leaves the secret store** but for the one call that
  uses it, and never reaches the portal.
- **Nothing a person typed is sent to Slack unescaped.** A title that
  reads `<!channel>` does not ping anyone.
