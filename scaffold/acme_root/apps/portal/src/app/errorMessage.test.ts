import { describe, expect, it } from "vitest";
import { ApiError, RequestTimeout } from "../api";
import { asSentence, errorMessage } from "./errorMessage";

describe("errorMessage", () => {
  it("says a refusal as a sentence, with no request id", () => {
    expect(errorMessage(new ApiError(401, "not_authenticated", "email or password is wrong", "req_1"), "no")).toBe(
      "Email or password is wrong.",
    );
    expect(errorMessage(new ApiError(409, "conflict", "already exists", "req_2"), "no")).toBe("Already exists.");
  });

  it("gives a fault its request id as a reference to quote", () => {
    expect(errorMessage(new ApiError(500, "internal", "something broke", "req_3"), "no")).toBe(
      "Something broke. Reference: req_3",
    );
    expect(errorMessage(new ApiError(502, "unknown_error", "Bad Gateway", null), "no")).toBe("Bad Gateway.");
  });

  it("uses the error's own words for a timeout or any other error", () => {
    expect(errorMessage(new RequestTimeout("POST", "/v1/api-keys", 30_000), "no")).toBe(
      "POST /v1/api-keys did not finish within 30000 ms",
    );
    expect(errorMessage(new Error("offline"), "no")).toBe("offline");
  });

  it("falls back to the caller's words when there are none", () => {
    expect(errorMessage(new Error(""), "The key was not created.")).toBe("The key was not created.");
    expect(errorMessage("what", "The key was not created.")).toBe("The key was not created.");
    expect(errorMessage(undefined, "The key was not created.")).toBe("The key was not created.");
    expect(errorMessage(new ApiError(400, "invalid", "", "req_4"), "Sign-in failed.")).toBe("Sign-in failed.");
  });
});

describe("asSentence", () => {
  it("capitalizes and closes a phrase, and leaves a sentence alone", () => {
    expect(asSentence("title is empty")).toBe("Title is empty.");
    expect(asSentence("Done already!")).toBe("Done already!");
    expect(asSentence("  ")).toBe("");
  });
});
