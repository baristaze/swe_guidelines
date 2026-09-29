# Tasks

The to-do items a team creates, works, and closes. This is one of the
seven kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Task**: a title, notes, a status (open or done), an assignee, a
  rank in the open list, a due date, and a version. It remembers
  who created it, who last changed it, and when its reminder went out.
- **Filter**: which tasks a list shows. *Team* shows every task of the
  org. *Mine* shows the tasks assigned to me, plus the unassigned ones I
  created.
- **Cursor**: where the previous page ended, so the next page starts
  right after it. The open list pages by rank, the done list by the
  time of the last change.
- **Page**: the tasks of one page and whether another page follows.
- **Attachment**: a [file](../media/README.md) kept with a task.
- **Import**: tasks made from the rows of a CSV file, a long job kept
  as an [orchestration](../orchestrations/README.md).
- **Archived task**: a done task the daily cleanup put away, because
  nobody changed it for the archive age (ninety days by default).
- **Bulk change**: one action, complete or reopen, over many tasks at
  once, and its answer: the tasks it changed and the ones it left alone,
  each with the reason.

## What can happen

- **Create.** A new task is open and goes to the top of the open list.
- **List the open tasks**, in the order the team arranged them, top
  first, a page at a time.
- **List the done tasks**, newest first, a page at a time.
- **Count the open tasks** a filter shows, without reading them: what a
  short list, like Slack's, says about the rest. The done tasks are
  counted the same way, without the archived ones: the number "Mark all"
  asks about.
- **Read one task.**
- **Edit**: the title, the notes, the assignee, the status. A task
  reopened from done goes back to the top of the open list.
- **Move** an open task right after another one, or to the top. The
  move writes that task and no other.
- **Complete or reopen many tasks at once**: the ones named, at most a
  thousand, or every task of the open or the done list in a scope. The
  change runs a hundred tasks a commit, so a list of thousands is many
  short writes, never one long one. It answers with the tasks it
  changed, in the order it changed them, and the ones it left alone. It
  names up to a thousand of each and counts all of them. Undoing it is
  the other action over the tasks it changed.
- **Delete.** The task is hidden, not erased. Its attachments are
  removed with it.
- **Attach a file**, list a task's files, and remove one. The upload
  itself, its confirm, and its download are the file's own.
- **Set, move, or clear the due date.** Setting it schedules one
  reminder on the morning of that day; moving it schedules a new one;
  clearing it schedules none.
- **Remind.** At nine in the morning of the due date, the task is marked
  reminded and every open screen of the org hears of it; so does the
  org's Slack channel, when the org has one. A screen that was away (its
  connection dropped, or its tab was hidden long enough to pause) hears
  of it when it comes back: each reminder by the task's title, or, past
  three, how many it missed.
- **Import** a CSV file. The file is uploaded as a
  [file](../media/README.md) of the `task_import` purpose, then the
  import starts, naming it. A worker reads the rows a hundred at a time;
  each batch creates its tasks and moves the import's cursor in one
  commit. The tasks go to the bottom of the open list, in the file's
  order. An imported task posts nothing to Slack.
- **Archive.** Once a day, per org, the cleanup archives the done tasks
  that nobody had changed for the archive age when the day began (in
  UTC), five hundred per step, each step one conditional write that
  only takes tasks still done and still that old. A task reopened or
  edited meanwhile is left alone. A task that turns that old during the
  day waits for the next day's cleanup.
- **List the archived tasks**, newest first, and **restore** one: it
  goes back to the top of the done list, and has the archive age again
  before the next cleanup takes it. Reopening an archived task restores
  it too.
- **Respace.** When a rank grows long, the sweep gives the tasks around
  it short ranks again, in the order they had.
- **Sweep.** Deleted tasks are erased for good after the retention,
  thirty days by default. A task's files are deleted before it is, so
  a file its delete could not reach is deleted then.

## The rules

- **The plan bounds the active tasks.** An active task is one that is
  neither done nor deleted. An org on a plan with a bound (ten on Free)
  is refused the task past it, and a reopen past it, with a refusal
  that names the plan that lifts the bound; a done or deleted task
  makes room again. Nothing is ever deleted for a bound
  ([billing](../billing/README.md)).
- **A bulk change is many edits.** Each task is changed under the
  rules a single edit applies, and each one on the version the change
  read, a moment before it writes. A task that is not the org's, is
  deleted, is already done (or already open), or changed between that
  read and the write is left alone and named, with the reason; the
  rest still change. The change names no versions of its own: what it
  asks is about the status, and a title edited meanwhile does not make
  "done" wrong. A reopen puts each task on top of the open list, as if
  they were reopened one at a time in the order given, so the last one
  given is on top. It stops at the plan: it reopens as many as the bound
  on active tasks allows and names the bound for the rest, the way an
  import parks at it. Each changed task is announced like a single
  edit. Nothing is posted to Slack: forty tasks done at once are not
  forty messages.
- **Every write names the version it read.** A task carries a version
  number, and every edit, move, and delete says which version the
  caller saw. If the stored task has moved on, the write is refused
  and nothing is merged; the caller reads again. An edit that raced a
  delete finds the task gone and cannot bring it back.
- **A change says which version it wrote.** The announcement of a
  task's change names the task and the version the change set, never a
  field. A screen that already shows that version, the one whose own
  write it was, has nothing to read (ADR 0061). A reminder and the
  daily archive name no version, and every screen reads the task.
- **Some fields are never the caller's.** An edit changes the title,
  the notes, the assignee, and the status. Who made the task, whether
  it is deleted, its place in the open list, and its version stay as
  stored, whatever the edit sends; a move places a task, and every
  write sets the version.
- **The rank.** Open tasks are ordered by a rank, an exact decimal
  number, top first. Between two ranks there is always another. A new
  task takes a whole number below the top one, so it lands on top. A
  task moved after another takes the shortest rank between that task's
  and the next one's, or the whole number after it when it is last. So
  a move writes one task and no other, and no other task's version
  moves.
- **Two tasks can share a rank.** Two people who add a task at the same
  moment can both take the rank above the same top. The list orders
  the two by their ids. A task moved after either of them goes after
  both.
- **A long rank is respaced.** Moves into one and the same gap add a
  digit every three moves or so. Past 24 digits after the point, the
  sweep takes the run of tasks around it (up to the nearest ranks of at
  most 12 digits, a hundred tasks each way at most) and gives them short
  ranks, spread evenly, in the order they had. It is one write; each
  task in it takes a new version and is announced like an edit. A run
  someone wrote meanwhile is left for the next sweep. The order a
  person sees never changes.
- **The position is the rank's float.** The table keeps each task's
  rank as a float too, its position, for the release before, which
  reads it. Nothing here reads or writes it: the database fills it from
  the rank. It goes in the release after this one.
- **A page is a page.** A page holds at most two hundred tasks, and
  "another page follows" is a fact about the rows, not a guess.
- **Assignment is checked when it changes.** Assigning a task to
  someone requires them to be a member now. Marking a task done or
  editing its title does not re-check an assignee who has since left;
  clearing the assignee is always allowed.
- **Mine is about nobody else.** The person the *mine* filter is about
  is always the caller.
- **A file is attached to a live task.** A deleted task, or another
  org's, takes no file and lists none; a file is removed only from the
  task it is attached to.
- **A task's delete does not wait on its files.** The task is deleted
  first and its attachments after it; when removing them fails, the
  delete still stands, the failure is logged and counted, and the files
  stay out of every list while still counting toward the storage used.
- **A due date is a date.** A task is due on a day, never at an hour.
  The API takes `YYYY-MM-DD` and refuses a time.
- **The reminder keeps the person's morning.** A date has no hour, so
  the reminder takes one: nine in the morning of the due date, in the
  time zone of the person the task is for. That is the assignee, or the
  creator when the task is unassigned. A person's time zone is the one
  their browser reports when they sign in to the portal; with none
  recorded, the reminder keeps UTC's morning. The zone is read when the
  reminder runs, so a task handed to someone else, or a person who
  moved, is still met on their own morning.
- **The import file is small and plain.** Its columns are `title`
  (needed), `notes`, `due_on` (`YYYY-MM-DD`), and `assignee_email` (a
  member of the org), by header name and in any order; any other column
  is ignored. A file is at most a megabyte and five thousand rows.
- **A bad row is skipped, a bad file fails.** A row with no title, a
  date that is not a date, or an assignee who is not a member makes no
  task; the import counts it and names the first twenty with the reason.
  A file past a bound, not a CSV file, or without a `title` column fails
  the import at once, and nothing is created.
- **An import stops at the plan, not past it.** When the next row would
  take the org past its plan's active tasks, the import parks there and
  keeps the tasks it made. A plan that rises wakes it; so does Resume,
  after tasks were finished. It goes on from the row it stopped at.
- **A row makes one task, however often its step runs.** A task's id is
  derived from the import and the row number, so a step that runs twice
  meets its tasks already there.
- **Archived is not deleted.** An archived task keeps its attachments
  and can be read by its id. It leaves the done list and every count
  shown by default; it never was an active task, so it frees no room.
- **A reminder is for the due date it was set for.** It goes out only
  while the task is open, not deleted, still due on that date, and not
  yet reminded, all checked in one write. So a reminder for a date that
  was moved or cleared, or for a task finished or deleted meanwhile,
  never goes out, and a reminder goes out once.
