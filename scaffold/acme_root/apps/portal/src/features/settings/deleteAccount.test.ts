import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { deleteAccount, type DeleteAccountEffects } from "./deleteAccount";

function effects(remove: DeleteAccountEffects["remove"]) {
  const forget = vi.fn<DeleteAccountEffects["forget"]>();
  const note = vi.fn<DeleteAccountEffects["note"]>();
  return {
    remove: vi.fn(async () => {
      // The token must still be held while the server is asked.
      expect(forget).not.toHaveBeenCalled();
      return remove();
    }),
    note,
    forget,
    leave: vi.fn<DeleteAccountEffects["leave"]>(() => {
      // The session is gone here, and the landing page told, before the browser leaves.
      expect(forget).toHaveBeenCalledTimes(1);
      expect(note).toHaveBeenCalledTimes(1);
    }),
  };
}

const PROVIDER_LOGOUT =
  "https://api.workos.com/user_management/sessions/logout?session_id=session_01&return_to=https%3A%2F%2Fapp.example%2Fsigned-out";

describe("deleteAccount", () => {
  it("deletes on the server first, then forgets the session and leaves to the signed-out page", async () => {
    const e = effects(() => Promise.resolve({ provider_logout_url: null }));
    await expect(deleteAccount(e)).resolves.toEqual({ deleted: true });
    expect(e.leave).toHaveBeenCalledWith(null);
  });

  it("goes through the provider's logout when the server names one", async () => {
    const e = effects(() => Promise.resolve({ provider_logout_url: PROVIDER_LOGOUT }));
    await deleteAccount(e);
    expect(e.leave).toHaveBeenCalledWith(PROVIDER_LOGOUT);
  });

  it("keeps the person signed in and says why when the server refuses", async () => {
    const refusal = new ApiError(409, "last_owner", "you are the last owner", "req_1", undefined, null, [
      { id: "o1", name: "Ajax", slug: "ajax" },
    ]);
    const e = effects(() => Promise.reject(refusal));
    const outcome = await deleteAccount(e);
    expect(outcome).toEqual({
      deleted: false,
      refusal:
        "You are the last owner of Ajax. Make someone else an owner, or delete the organization, first. " +
        "Both are done in that organization's Settings.",
      stranded: [{ id: "o1", name: "Ajax", slug: "ajax" }],
    });
    expect(e.forget).not.toHaveBeenCalled();
    expect(e.note).not.toHaveBeenCalled();
    expect(e.leave).not.toHaveBeenCalled();
  });
});
