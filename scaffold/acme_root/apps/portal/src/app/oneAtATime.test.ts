import { describe, expect, it } from "vitest";
import { inFlight, oneAtATime } from "./oneAtATime";

describe("oneAtATime", () => {
  it("sends one request for clicks that arrive together, and drops the rest", async () => {
    const gate = inFlight();
    let sent = 0;
    const exchange = () =>
      oneAtATime(gate, async () => {
        sent += 1;
        await Promise.resolve();
        return "ses_new";
      });

    const answers = await Promise.all([exchange(), exchange(), exchange()]);

    expect(sent).toBe(1);
    expect(answers).toEqual(["ses_new", undefined, undefined]);
  });

  it("takes the next choice once the first has its answer, refused or not", async () => {
    const gate = inFlight();
    await expect(oneAtATime(gate, () => Promise.reject(new Error("refused")))).rejects.toThrow("refused");
    expect(gate.busy).toBe(false);
    await expect(oneAtATime(gate, () => Promise.resolve("again"))).resolves.toBe("again");
  });
});
