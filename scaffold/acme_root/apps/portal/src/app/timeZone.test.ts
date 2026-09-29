import { describe, expect, it } from "vitest";
import { browserTimeZone, zoneToSend } from "./timeZone";

describe("time zone", () => {
  it("sends the browser's zone when the identity holds none or another", () => {
    expect(zoneToSend("Europe/Istanbul", null)).toBe("Europe/Istanbul");
    expect(zoneToSend("Europe/Istanbul", undefined)).toBe("Europe/Istanbul");
    expect(zoneToSend("Asia/Tokyo", "Europe/Istanbul")).toBe("Asia/Tokyo");
  });

  it("sends nothing when the identity holds it already, or the browser names none", () => {
    expect(zoneToSend("Europe/Istanbul", "Europe/Istanbul")).toBeNull();
    expect(zoneToSend(null, "Europe/Istanbul")).toBeNull();
    expect(zoneToSend(null, null)).toBeNull();
  });

  it("reads an IANA name from the browser", () => {
    const zone = browserTimeZone();
    expect(zone === null || /^[A-Za-z_]+(\/[A-Za-z0-9_+-]+)*$/.test(zone)).toBe(true);
  });
});
