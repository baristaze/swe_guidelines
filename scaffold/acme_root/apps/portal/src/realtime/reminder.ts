// A reminder as one flow with its effects handed in, so it runs in a test
// without React. The push carries the task's id only, so the title is read
// from the server; a task that cannot be read (deleted since, or the network
// failed) is still announced, without its title.
//
// A reminder that fired while the portal was offline is announced too, once
// the replay after the reconnect has read it back from the stream: one
// notice each, by title, up to MISSED_REMINDERS_NAMED of them, and past that
// one notice that counts them, which reads nothing.
import type { TaskView } from "../api";

export interface ReminderEffects {
  /** Reads the task the push names. */
  readTask: (id: string) => Promise<TaskView>;
  /** Shows one notice. */
  notify: (message: string) => void;
}

export const UNNAMED_REMINDER = "Reminder: a task is due.";

export function reminderMessage(title: string | null): string {
  const trimmed = title?.trim();
  return trimmed ? `Reminder: ${trimmed}` : UNNAMED_REMINDER;
}

export async function announceReminder(taskId: string, effects: ReminderEffects): Promise<string> {
  let title: string | null;
  try {
    title = (await effects.readTask(taskId)).title;
  } catch {
    title = null;
  }
  const message = reminderMessage(title);
  effects.notify(message);
  return message;
}

/** How many reminders read back by one replay are each announced by name. */
export const MISSED_REMINDERS_NAMED = 3;

export function missedRemindersMessage(count: number): string {
  return `You missed ${count} reminders while you were away.`;
}

/** Announces the reminders one replay read back, in stream order: each by its
 * task's title, one read a task, when there are at most
 * MISSED_REMINDERS_NAMED; otherwise one notice that counts them. A task
 * reminded twice counts once. Returns the messages shown. */
export async function announceMissedReminders(
  taskIds: readonly string[],
  effects: ReminderEffects,
): Promise<string[]> {
  const distinct = [...new Set(taskIds)];
  if (distinct.length > MISSED_REMINDERS_NAMED) {
    const message = missedRemindersMessage(distinct.length);
    effects.notify(message);
    return [message];
  }
  const shown: string[] = [];
  for (const id of distinct) shown.push(await announceReminder(id, effects));
  return shown;
}
