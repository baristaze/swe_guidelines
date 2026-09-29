# Acme portal

The product's browser app: React, TypeScript, Vite, TanStack Query,
Zustand, one realtime channel.

## Conventions

- Feature code never calls `fetch` and never imports `schema.d.ts`;
  everything goes through `src/api/` (the types generated from the
  committed `openapi.json` behind the facade `types.ts`, and the one
  transport client; `src/app/api.ts` holds the one instance). ESLint
  enforces both. `make openapi` regenerates `schema.d.ts`. The object
  store is the one other place a request goes, through a URL the API
  signed and handed over: `src/api/store.ts` posts a form to it or
  fetches a link from it, with a deadline of its own and nothing of the
  API client's (no bearer, no retry), since the form or the link is the
  credential.
- A task's files are `src/features/attachments/`, shown in the task's
  open view: dropped or picked, started on the API, posted straight to
  the store, confirmed, listed with name, size, and type, downloaded and
  saved under the file's own name, removed. Each file with a preview
  shows it inline, through the store's short-lived inline link: an image
  as a thumbnail that opens larger in the page, a video and a sound in
  the browser's player, a PDF in a frame; anything else (text, documents,
  archives) is download-only, and download stays beside every preview.
  Someone who cannot write opens a task's files read-only with its
  `files` link. The flows
  (`transfer.ts`) take their effects as arguments and run in a test
  without React. Where the store cannot take a form, the bytes go
  through the API instead. The settings page shows the org's storage
  used.
- An import is `src/features/imports/`: the tasks page's Import action (a
  button of its own beside the page's heading; the quick-add form stays one
  text box) opens a small dialog that picks a CSV file. The file goes up
  the way an attachment does (`importFlow.ts`, its effects handed in), and
  the import starts naming it. The rows are read in the worker; the page
  follows the org's newest import by what the channel pushes
  (`orchestrations.orchestration.updated`, whose entity keys
  `queries/imports.ts`), with no polling: a progress line (created N of M,
  skipped K), and when the import parks on the plan's active tasks, the
  one upgrade dialog and Resume. An import that ended shows what it did
  until dismissed. Under the done list, "Show archived" opens the done
  tasks the daily cleanup archived (`ArchivedTasks.tsx`), each with
  Restore.
- The app's one retry is the transport client's, and no other layer has
  one: TanStack Query's is off in `src/app/queryClient.ts`, so a failing
  API sees these attempts and no multiple of them. Only a failure that
  can differ on a second attempt goes again (the deadline, a connection
  that failed before an answer, and a 502, 503, or 504); a refusal is a
  decision and is told to the caller. Only a read and a creating POST
  under its idempotency key may be sent twice, so a write the gateway
  does not record the outcome of is sent once. The rules and the curve
  are `src/api/retry.ts`: the delay doubles, half of each wait is jitter,
  a `Retry-After` the server sends lengthens a wait to it (never past the
  cap), and the count and the first delay come from the runtime config.
- Server state lives in TanStack Query (`src/queries/`), with keys from
  `src/queries/keys.ts`. The first key element is the entity name the
  server pushes, so a push invalidates by convention; an entity the
  convention does not reach on its own (a membership, read through
  `me` and the person's list of places; a user, read through `me` as
  well as its own list) is named in the router's table instead.
- The task lists are written, not read again. A write answers with the
  task as the server wrote it, and that answer goes into every cached
  list, in every scope, at once (`src/queries/taskCache.ts`). A live
  push about a task names its id and the version its change wrote, never
  a field. A tab that already placed the task at that version reads
  nothing: the tab that made the write holds its answer, and the push is
  about that answer. Any other tab reads that one task
  (`GET /v1/tasks/{id}`) and places it the same way; a 404 takes it
  out. A push that names no version (the reminder, the daily archive)
  is always read (ADR 0061). Where it goes is one pure function (`src/queries/taskPlacement.ts`):
  the open list by rank then id, the done list newest first, the
  `mine` scope by the server's rule, archived and deleted tasks in
  neither. A list is the window the page loaded, and its last row is
  the one the next page's cursor names: a task that sorts past it is
  left out, and a change the window cannot answer (a removal that
  leaves a window with more behind it short, the last row moving)
  reads that one list again. A task placed into a full window stays
  held past what the page shows, and shows on Show more. An answer
  older than one already placed is dropped, by the task's version.
  Pushes are gathered until 100 ms pass without one, and for 500 ms at
  most (`src/realtime/taskHints.ts`): a task pushed twice is read once,
  and past twenty tasks in one window (an import step, a bulk change,
  the sweep's respace of a run of ranks) the lists are read once instead. Whether a task is held is asked when the window closes, so
  the push of a tab's own write that beats the write's answer is still
  skipped, and only the tasks left to read count toward the twenty. The other features still invalidate on a push.
- A query a push reaches (`isKeptFresh` in `src/realtime/router.ts`: the
  tasks, `me`, the members, the plan, the files, the invitations, the
  keys, an import, the Slack installation, the person's places) is fresh
  for five minutes and is not read again when the tab regains focus,
  while the socket is open (`src/app/queryClient.ts`). The push says
  when it changed, and a push the socket missed comes back in the replay
  that follows. A query no push names, such as the person's identity,
  is fresh for ten seconds and read again on focus. While the socket is
  connecting, degraded, paused, or closed, every query is.
- Client state lives in Zustand (`src/store/`): the session token, the
  connection status, the transient notices a failed write leaves
  (`notices.ts`, rendered by `src/app/Notices.tsx` over the kit's
  `Banner`; a view-model never swallows a mutation error), the upgrade
  dialog a write refused for a plan's bound opens instead of a notice
  (`upgrade.ts`, opened from the query cache's one mutation error hook and
  rendered once by `src/features/billing/UpgradeDialog.tsx`), and the
  preferences kept across visits (`preferences.ts`, the task scope, the
  theme, and the folded sections per org, persisted in local storage). The token is kept in memory and in the
  tab's session storage, so a reload survives and a closed tab forgets;
  never in local storage, which is for preferences only. No feature
  reads a storage directly; a store does.
- One screen is `src/features/<screen>/`: `<Screen>Page.tsx` renders,
  `use<Screen>Vm.ts` decides, `<screen>Model.ts` computes and is unit
  tested without React.
- `src/realtime/RealtimeProvider.tsx` owns the one socket; the loop
  itself (ticket, reconnect with backoff, the stream cursor and its
  replay) is `channel.ts`, without React, run in its test over a fake
  socket and fake timers. A replay refused as `stream_truncated` (the
  stream is trimmed past the cursor) refreshes every query once and moves
  the cursor to the head the refusal names. The reconnect delay doubles to a cap and half
  of each wait is jitter, because a socket drops for a shared reason: a
  bare curve would bring every tab back at the same instant. A socket
  counts as connected once the hello frame arrives, so a server that
  accepts and closes at once still backs off. A tab hidden five minutes
  (`HIDDEN_PAUSE_MS` in `timeouts.ts`) closes its socket and says paused:
  an open socket costs the server more than a reconnect by then. A pause
  is no failure, so it schedules no reconnect, counts no failed cycle,
  polls nothing, and shows no banner. The page turning visible, a
  `pageshow` (a restore from the back/forward cache too), a `focus`, or
  `online` resumes it with a fresh ticket, and the open replays from the
  cursor; many at once make one connect (`pageVisibility.ts` listens, the
  channel decides). A session that ended meanwhile is refused its ticket
  with a 401, which signs out. The first hello's catch-up
  reads the stream's last page and routes what was produced since the
  page began reading; it refreshes every query only when that page cannot
  tell, so a page load reads each query once. Envelopes (`envelopes.ts`)
  route into the query cache (`router.ts`), never into components. A
  record read back from the stream (a replay, the first catch-up) is
  routed once per entity, so a task record there reads the task lists
  whole, as every other entity's queries are. A reminder there is kept
  whatever record of its task follows it, and announced once the
  read-back ends: each by its title up to three, and past three one
  notice that counts them, "You missed 5 reminders while you were away"
  (`reminder.ts`, ADR 0075). The
  ping interval comes from
  `deployment/realtime-timeouts.json`.
- A person signs in through WorkOS AuthKit, which the API fronts. `/login`
  is the address WorkOS sends a person to when a sign-in did not start here
  (an invitation's link, a bookmarked sign-in page), and it starts one at
  once, with no form (`src/features/sign_in/`): it keeps a fresh random
  `state` in the tab's session storage (`src/store/signInState.ts`), with
  the page to come back to and an invitation's token, asks the API for the
  authorization URL for this origin's `/auth/callback`, and sends the
  browser there. `/auth/callback` goes on only with a state this tab
  stored, consumed once, so a code started anywhere else is refused; it
  hands the code to the API, which exchanges it with WorkOS server-side,
  and a person nobody knew is signed up by it, with their personal org. A
  tab that just signed out waits on `/login` for the person to ask, rather
  than being signed straight back in. Sign out is the last item of the
  account menu. It asks the API to
  end the session with a return to this origin's `/signed-out`; when the
  API answers WorkOS's logout, the browser goes there last
  (`src/app/signOut.ts`), WorkOS ends its AuthKit session,
  and sends the browser back to `/signed-out`, the sign-in page waiting
  with "You are signed out." So the next sign-in on this browser asks who
  it is. A session from the local sign-in stays on the portal. `/sign-in` and `/sign-up` still lead
  there. The local stack also serves `/login/dev`, a sign-in by address
  alone for the seeded people and anyone a developer names, offered only
  when the runtime config says `devSignIn` (true locally, absent in the
  cloud) and refused by a deployed API.
- One tenant at a time. Every sign-in answers with the person's places:
  one goes straight in, several show a picker with the personal org first,
  none says so. The exchange turns the choice into the one session the tab
  holds. One choice sends one exchange: while it is in flight the picker's
  buttons are disabled, and a click that still gets through is dropped
  (`src/app/oneAtATime.ts`). A sign-in over a tab that is still signed in
  replaces the session the tab held (`src/app/takeUpSignIn.ts`): the new
  one is taken up first, then the old one is ended at the server with its
  own token, the logout a sign-out makes. That is best effort, and a
  session the server could not end lapses when it expires. The logout's
  answer may name the identity provider's logout; the tab does not go
  there, since the person is signing in, not out. The org chip in the chrome (`src/app/OrgChip.tsx`) shows the
  current org and marks a personal one. Its name goes home, to the task
  list; its caret opens a menu: the other places to
  switch to, and a new team org (`/orgs/new`, `src/features/new_org/`: a
  name and an optional short name, which the server makes from the name
  when it is left empty). A switch presents the current session to the
  exchange, which the server ends in the same write. The server closes
  that session's socket with 4401 as it ends it, and the close often
  arrives before the exchange's answer. So the switch holds the session
  while the exchange runs (`holdSession` in `src/app/forgetSession.ts`): a
  4401 or a 401 for it is noted, not acted on. With the answer in hand,
  `adoptSession` drops every cached answer of the old tenant and puts the
  new token in place of the old in one write, so the tab never holds no
  token on the way. Only then does the hold end. An exchange that fails
  after its session was refused signs the tab out; any other refusal
  leaves the tab where it was and says why. A new team org is created
  under an idempotency key and then switched into the same way. The
  signed-in shell is keyed by the org (`src/app/routes.tsx`), so a switch
  mounts it afresh: the page on show, its queries, its open dialogs and
  drafts, and the realtime provider all start over in the new org, and
  nothing of the old one stays on screen. The new provider opens a new
  socket; a later 4401 from the old socket signs nothing out, since it
  belongs to a token the tab no longer holds.
- The chrome (`src/app/AppNav.tsx`) is one bar on every signed-in page,
  with no tabs. On the left, the org chip. On the right, a gear that goes
  to Settings, and the account menu (`src/app/AccountMenu.tsx`): the
  person's email (the part before the @ on a narrow window) opens it on a
  click, never on hover. It shows the email and the current org, and holds
  Settings, the theme (System, Light, Dark, the one in use checked), and
  Sign out. Both menus are the kit's `Menu`: Enter, Space, or an arrow
  opens it, the arrows, Home, and End move, Escape closes it and puts the
  keyboard back on its button, and a click anywhere else closes it.
  Settings and Billing open with "← Tasks" above their title, the way
  back to the list.
- The tasks page's heading is the list on screen. When the org has more
  than one member, of either kind, it is a switch, My tasks | Team. When
  the person is alone in the org it is "My tasks", with no switch, and
  the page shows the `mine` list, which the server's rule (a task
  assigned to the person, or unassigned and made by them) makes every
  task of an org of one. A saved Team pick holds only where the switch
  shows. The count is the member list the rows already name people
  from, read whole, so it costs no read of its own (`scopeModel.ts`).
- Tasks. Adding one is a single text box: type the title and press Enter,
  or click Add. The due date is set by editing the task, with a date
  picker and a link that clears it. A due date is a date, never a time:
  the row says "due Today", "due Tomorrow", "due Fri" within the week
  ahead, or "due Sep 30" further out, and "Overdue" once the day has
  passed with no reminder out. The reminder goes out at nine in the
  morning of the date, in the time zone of the person the task is for.
- The tasks page's two sections, Open and Done, fold under their titles.
  The title is a button (`aria-expanded`), so a click, Enter, or Space
  folds it, and the caret turns with it. Open starts unfolded and Done
  folded; after that each section stays as the person left it, per org,
  in the preferences (`folded`). The archive folds with Done.
- Many tasks at once (`src/features/tasks/`: `selectionModel.ts` and
  `bulkModel.ts` are pure, `useBulkVm.ts` decides). ⌘-click (Ctrl-click)
  adds a row to the selection or drops it, Shift-click reaches from the
  row picked last, and on touch a finger resting on a row for half a
  second picks it. Once a selection is open, a plain click or tap picks
  too. From the keyboard, each list is a grid with one tab stop: the
  arrows move between rows, Space picks, and Shift reaches. Escape lets
  the selection go. A selected row is tinted, with `aria-selected`; its
  box still means done or open and is never a second box. A selection
  lives in one section of the list on screen; a pick in the other
  section starts over there, and a switch of scope or org starts with
  none. There is no drag to select, since dragging a row reorders the
  open list. While anything is selected, a bar at the foot of the window
  says how many and offers the one action that applies: Mark done for
  open tasks, Reopen for done ones. Each section's ⋯ menu (the kit's
  `Menu`) has Select all, which selects every task of the section in the
  scope, loaded or not, counted on the server (`GET /v1/tasks/count`),
  and, in red, "Mark all as done…" on Open and "Reopen all…" on Done,
  which ask first with that count. Every change is one call,
  `POST /v1/tasks/bulk`, under an idempotency key: the rows move at once,
  and the lists are read again when the server answers, since the answer
  names ids. A toast then says what changed, with Undo for ten seconds;
  Undo is the other action over exactly the ids the answer named, sent
  bottom first for a reopen, so the list reads as it did. A change of
  more tasks than the answer names (a thousand) offers no Undo. A reopen
  past the plan's active tasks changes what it can and opens the upgrade
  dialog for the rest. The toast is the notices store's (`notices.ts`,
  its `done` tone and its one action), drawn with the kit's `Toast`.
- The person's time zone. After sign-in, the shell (`TimeZoneSync` in
  `src/app/useTimeZoneSync.ts`) reads `/v1/me/identity` and, when the
  browser's IANA zone differs from the one held there, sends it once with
  `PATCH /v1/me/identity`. It is quiet: a refusal is dropped and the page
  never waits on it.
- Settings, for a member who manages members: each member's role, which
  a small menu on the member's row changes (`RoleControl.tsx`). It holds
  the roles the server's rules let the member give: never their own
  role, never a member above their own role, never a role above it, so
  an owner makes someone else an owner and an admin gives roles up to
  admin (`settingsModel.grantableRoles`). It opens on a click, Enter,
  Space, or the arrows, and closes on Escape or outside. A refusal is
  said as a notice. An invitation by email and
  role (at most their own, never owner), which WorkOS sends, and the
  pending ones to send again or revoke. A team org's settings also open
  WorkOS's admin portal, where the org's admin connects their identity
  provider for single sign-on or proves a domain; a personal org has none.
- "Delete my account", the last card of Settings, for everyone. The
  person types their email to confirm, and the card says what goes: the
  account, the personal org with its tasks and files, and their place
  in every team org, "gone now, and gone from backups within 7 days".
  A refusal stays on the card: the last owner of a team org is told
  which orgs (`last_owner` in the error envelope), and to make someone
  else an owner or delete the organization. When the org the tab is in
  is one of them, the card links to its members and to its deletion. Once deleted, the tab
  forgets its session and notes the deletion in its session storage,
  and the browser goes through WorkOS's logout, when the API names one,
  to `/signed-out`, which says "Your account is deleted." once
  (`src/features/settings/deleteAccount.ts`).
- "Delete this organization", above "Delete my account", for an owner of
  a team org only. The owner types the org's name to confirm, and the
  card says what goes for everyone in it: its tasks and files, its
  members and their keys, its plan, and its Slack app, kept 30 days and
  then purged. The server answers with a session in the owner's personal
  org, which the tab takes up, as a switch does, and opens the task list
  there. With no session in the answer, the tab signs in again
  (`src/features/settings/deleteOrg.ts`).
- "Former member". A task names its maker and its assignee from the
  org's member list, read whole; an id the list does not hold is a
  person who left the org or deleted their account, and reads "Former
  member" (`tasksModel.nameOf`).
- Design tokens and the kit live in `src/design/`; the operator console,
  when it is built (ADR 0010), imports them from here. A colour or a
  shadow token is a CSS custom property, and `theme.css` gives each its
  light and its dark value, so an inline style follows the theme with no
  code of its own. `kit.css` holds what inline styles cannot say (hover,
  focus, the dragged row). Feature code styles through the tokens and the
  kit, never a literal colour. Every text colour meets WCAG AA in both
  themes. The theme follows the system until the person picks one in the
  account menu (System, Light, Dark); the pick is a
  `data-theme` attribute on the root, set before the app renders. The
  typeface is Inter, bundled with the build (`@fontsource-variable/inter`),
  so the page loads no font from another origin.
- Every item of a kit menu carries a 16-pixel icon before its words: the
  gear, the theme's screen, sun, and moon, and the door of Sign out; a
  person or two for an org, a plus for a new one; the list, the double
  check, and the arrow back of a tasks section; a crown, a shield, a
  person, and an eye for the roles. The icons are inline SVG in
  `src/design/kit/icons.tsx`, Lucide's drawings under its ISC license
  (four of them Feather's, under MIT; the gear is the bar's own). They take the muted text colour and are
  hidden from a screen reader, which says the words alone. Each sits in a
  slot of fixed width, so the words line up down a menu, and a radio
  item's check stays at the far end.
- The tab's icon is the product's mark, the company site's
  `favicon.svg`, in `public/` with two drawings of it:
  `favicon-32.png` for a browser that takes no SVG icon, and
  `apple-touch-icon.png`, 180 by 180 on the mark's indigo, for a home
  screen. `scripts/favicons.sh apps/portal/public` draws the two again
  from the SVG, through headless Chrome, when the mark changes; the site
  runs it on `apps/site/public`. The build copies `public/` to the root
  of `dist/`, and the deploy uploads it with the entry points, revalidated
  on every load. There is no web manifest: the portal is not installed as
  an app, and the icons need none.

## Run

```bash
pnpm install
pnpm --filter @acme/portal dev     # http://localhost:5173; /v1 forwards to the API on 127.0.0.1:8000
pnpm --filter @acme/portal test
```

The page calls the API on its own origin, locally as in the cloud. The dev
server forwards `/v1`, the realtime socket included, to the API on
`127.0.0.1:8000`; `ACME_PORTAL_API_TARGET` points it at another. So no
request the page makes is cross-origin, and no browser sends a preflight.

`make stack-up` from the repository root also serves a production build
in a container at http://localhost:55173, from
`deployment/docker/portal.Dockerfile`. Its nginx forwards `/v1` to the
`api` container the same way. `VITE_API_URL` is compiled into that bundle
and is empty, the page's own origin; a value there names another API and
needs a rebuild, which `make stack-up` does. After `make seed`, sign in at `/login/dev` as
`owner@example.test` (owner) or `bob@example.test` (member), by address
alone; `/login` signs a person in through WorkOS, once the API holds the
Acme App application's API key. See the root README for every local URL.

## Configuration

`src/app/config.ts` loads `/config.json` before anything renders. In the
cloud that file exists, written per environment by Terraform, so one build
serves every environment. Its `apiUrl` is empty, which means the page's own
origin: the portal's CloudFront distribution serves the API's `/v1/*` paths
from the API's load balancer. Locally there is none, and `VITE_API_URL`
(empty, the page's origin, by default), `VITE_SENTRY_DSN`, and
`VITE_SENTRY_ENVIRONMENT` apply instead. The DSN is
the product's one tracker project in every environment; the environment the
page sends on each event is what separates them. The file may
also name `requestTimeoutMs`, the deadline the transport client puts on every
call; without it, and locally, the deadline is 30 seconds. It may name
`retryAttempts` and `retryBaseDelayMs` the same way, the extra attempts a
retryable failure gets and the wait before the first of them; without them the
client makes 2 extra attempts, the first after 125 to 250 ms and the second
after 250 to 500 ms. `retryAttempts: 0` sends every call exactly once. `devSignIn: true` offers
the local sign-in at `/login/dev`; a deployed config leaves it out, and
locally, with no file, it is on unless `VITE_DEV_SIGN_IN=false`. How the
build reaches the cloud is in `deployment/terraform/modules/README.md`.
