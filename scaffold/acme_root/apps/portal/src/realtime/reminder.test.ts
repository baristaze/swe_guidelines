import type { TaskView } from "../api";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import {
  announceMissedReminders,
  announceReminder,
  MISSED_REMINDERS_NAMED,
  missedRemindersMessage,
  reminderMessage,
  UNNAMED_REMINDER,
  type ReminderEffects,
} from "./reminder";

const task = (title: string): TaskView => ({
  id: "t1",
  title,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  created_at: "2026-09-22T10:00:00Z",
  updated_at: "2026-09-22T10:00:00Z",
  created_by: "ann",
  deleted_at: null,
  due_on: "2026-09-22",
  reminded_at: "2026-09-22T12:00:01Z",
  version: 3,
});

describe("announceReminder", () => {
  it("reads the task the push names and says its title", async () => {
    const readTask = vi.fn<ReminderEffects["readTask"]>(async () => task("Call the bank"));
    const notify = vi.fn<ReminderEffects["notify"]>();
    await expect(announceReminder("t1", { readTask, notify })).resolves.toBe("Reminder: Call the bank");
    expect(readTask).toHaveBeenCalledWith("t1");
    expect(notify).toHaveBeenCalledWith("Reminder: Call the bank");
  });

  it("still announces a task it cannot read, without the title", async () => {
    const notify = vi.fn<ReminderEffects["notify"]>();
    const readTask = async () => {
      throw new ApiError(404, "not_found", "no such task", "req_1");
    };
    await announceReminder("t1", { readTask, notify });
    expect(notify).toHaveBeenCalledWith(UNNAMED_REMINDER);
  });

  it("words a blank title as an unnamed task", () => {
    expect(reminderMessage("  Ship it ")).toBe("Reminder: Ship it");
    expect(reminderMessage("   ")).toBe(UNNAMED_REMINDER);
    expect(reminderMessage(null)).toBe(UNNAMED_REMINDER);
  });
});

describe("announceMissedReminders", () => {
  const titles: Record<string, string> = { t1: "Call the bank", t2: "Water the plants", t3: "Pay rent", t4: "Book flights" };
  const effects = () => ({
    readTask: vi.fn<ReminderEffects["readTask"]>(async (id) => ({ ...task(titles[id]!), id })),
    notify: vi.fn<ReminderEffects["notify"]>(),
  });

  it("names each missed reminder, in stream order, up to three", async () => {
    const e = effects();
    await expect(announceMissedReminders(["t2", "t1", "t3"], e)).resolves.toEqual([
      "Reminder: Water the plants",
      "Reminder: Call the bank",
      "Reminder: Pay rent",
    ]);
    expect(e.notify.mock.calls.map(([message]) => message)).toEqual([
      "Reminder: Water the plants",
      "Reminder: Call the bank",
      "Reminder: Pay rent",
    ]);
  });

  it("counts them in one notice past three, and reads no task", async () => {
    const e = effects();
    await announceMissedReminders(["t1", "t2", "t3", "t4"], e);
    expect(MISSED_REMINDERS_NAMED).toBe(3);
    expect(e.notify).toHaveBeenCalledTimes(1);
    expect(e.notify).toHaveBeenCalledWith("You missed 4 reminders while you were away.");
    expect(e.readTask).not.toHaveBeenCalled();
  });

  it("counts a task reminded twice once", async () => {
    const e = effects();
    await announceMissedReminders(["t1", "t2", "t1", "t2"], e);
    expect(e.notify.mock.calls.map(([message]) => message)).toEqual([
      "Reminder: Call the bank",
      "Reminder: Water the plants",
    ]);
  });

  it("shows nothing for none", async () => {
    const e = effects();
    await expect(announceMissedReminders([], e)).resolves.toEqual([]);
    expect(e.notify).not.toHaveBeenCalled();
    expect(missedRemindersMessage(12)).toBe("You missed 12 reminders while you were away.");
  });
});
