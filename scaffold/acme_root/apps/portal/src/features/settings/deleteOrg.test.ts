import { describe, expect, it, vi } from "vitest";
import { ApiError, type IssuedSessionView } from "../../api";
import { deleteOrg, type DeleteOrgEffects } from "./deleteOrg";

const HOME: IssuedSessionView = {
  token: "ses_home",
  expires_at: "2026-10-01T00:00:00Z",
  org: { id: "p1", name: "Ann", slug: "ann-x1", kind: "personal", created_at: "2026-09-01T00:00:00Z" },
  user: { id: "u9", email: "ann@example.test", display_name: "Ann", created_at: "2026-09-01T00:00:00Z" },
  role: "owner",
};

function effects(remove: DeleteOrgEffects["remove"], refusedWhileHeld = false) {
  const release = vi.fn(() => refusedWhileHeld);
  const adopt = vi.fn<DeleteOrgEffects["adopt"]>();
  const forget = vi.fn<DeleteOrgEffects["forget"]>();
  return {
    remove: vi.fn(async () => {
      // The token must still be held while the server is asked.
      expect(adopt).not.toHaveBeenCalled();
      expect(forget).not.toHaveBeenCalled();
      return remove();
    }),
    hold: vi.fn(() => ({ release })),
    release,
    adopt,
    forget,
    land: vi.fn<DeleteOrgEffects["land"]>(),
  };
}

describe("deleteOrg", () => {
  it("deletes on the server first, then takes up the personal org's session and lands home", async () => {
    const e = effects(() => Promise.resolve({ session: HOME }));
    await expect(deleteOrg(e)).resolves.toEqual({ deleted: true });
    expect(e.adopt).toHaveBeenCalledWith(HOME);
    expect(e.adopt.mock.invocationCallOrder[0]).toBeLessThan(e.release.mock.invocationCallOrder[0]!);
    expect(e.forget).not.toHaveBeenCalled();
    expect(e.land).toHaveBeenCalledWith("home");
  });

  it("forgets the session and sends the owner to sign in when no session came back", async () => {
    const e = effects(() => Promise.resolve({ session: null }));
    await expect(deleteOrg(e)).resolves.toEqual({ deleted: true });
    expect(e.adopt).not.toHaveBeenCalled();
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.land).toHaveBeenCalledWith("sign_in");
  });

  it("keeps the owner where they are and says why when the server refuses", async () => {
    const refusal = new ApiError(422, "validation_failed", "type the organization's name to delete it", "req_1");
    const e = effects(() => Promise.reject(refusal));
    await expect(deleteOrg(e)).resolves.toEqual({
      deleted: false,
      refusal: "Type the organization's name to delete it.",
    });
    expect(e.release).toHaveBeenCalledTimes(1);
    expect(e.forget).not.toHaveBeenCalled();
    expect(e.land).not.toHaveBeenCalled();
  });

  it("signs the tab out when the session it held is gone", async () => {
    const e = effects(() => Promise.reject(new ApiError(401, "invalid_credential", "session revoked", "req_2")));
    await expect(deleteOrg(e)).resolves.toEqual({ deleted: false, refusal: null });
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.land).toHaveBeenCalledWith("sign_in");
  });
});
