// @vitest-environment jsdom
// The create-team entry over a fake transport: the org is created under an
// idempotency key, then the session this tab holds is exchanged for one in
// the new org, and the tab lands on its task list. A refusal is said and
// nothing is switched.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, type IssuedSessionView, type MembershipChoiceView } from "../../api";
import { useSessionStore } from "../../store/session";
import { useNewOrgVm } from "./useNewOrgVm";

interface Sent {
  path: string;
  body: unknown;
  options: { idempotencyKey?: string } | undefined;
}

const net = vi.hoisted(() => {
  const sent: Sent[] = [];
  const answers = new Map<string, () => Promise<unknown>>();
  const post = (path: string, body: unknown, options?: { idempotencyKey?: string }) => {
    sent.push({ path, body, options });
    const answer = answers.get(path);
    return answer ? answer() : Promise.reject(new Error(`no answer for ${path}`));
  };
  const navigate = vi.fn();
  return { sent, answers, post, navigate };
});

vi.mock("../../app/api", () => ({ api: { post: net.post } }));
vi.mock("react-router-dom", () => ({ useNavigate: () => net.navigate }));

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
let root: ReturnType<typeof createRoot>;

const org = { id: "o2", name: "Dee's Bakery", slug: "dee-s-bakery-1a2b", kind: "team" as const, created_at: "2026-09-01T00:00:00Z" };
const user = { id: "u2", email: "dee@example.test", display_name: "Dee", created_at: "2026-09-01T00:00:00Z" };
const place: MembershipChoiceView = { org, user, role: "owner" };
const session: IssuedSessionView = { token: "ses_team", expires_at: "2026-09-02T00:00:00Z", org, user, role: "owner" };

const held: { vm?: ReturnType<typeof useNewOrgVm> } = {};
const vm = () => held.vm!;

function Probe() {
  const current = useNewOrgVm();
  useEffect(() => {
    held.vm = current;
  });
  return null;
}

async function mount() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(Probe)));
  });
}

beforeEach(() => {
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  held.vm = undefined;
  net.sent.length = 0;
  net.answers.clear();
  net.navigate.mockReset();
  useSessionStore.getState().setSession("ses_home", "dee-9z8y");
});

afterEach(async () => {
  await act(async () => root.render(null));
});

it("creates the org under a key, switches into it, and lands on its tasks", async () => {
  net.answers.set("/v1/orgs", () => Promise.resolve(place));
  net.answers.set("/v1/auth/sessions", () => Promise.resolve(session));
  await mount();
  await act(async () => vm().setName(" Dee's Bakery "));
  expect(vm().slugPlaceholder).toBe("dee-s-bakery");
  await act(async () => vm().submit());
  expect(net.sent.map((s) => [s.path, s.body])).toEqual([
    ["/v1/orgs", { name: "Dee's Bakery" }],
    ["/v1/auth/sessions", { org_id: "o2" }],
  ]);
  expect(net.sent[0]!.options?.idempotencyKey).toBeTruthy();
  expect(useSessionStore.getState().token).toBe("ses_team");
  expect(net.navigate).toHaveBeenCalledWith("/", { replace: true });
});

it("sends the short name only when one was typed, and refuses a malformed one without a call", async () => {
  net.answers.set("/v1/orgs", () => Promise.resolve(place));
  net.answers.set("/v1/auth/sessions", () => Promise.resolve(session));
  await mount();
  await act(async () => vm().setName("Bakery"));
  await act(async () => vm().setSlug("Not A Slug"));
  await act(async () => vm().submit());
  expect(net.sent).toEqual([]);
  expect(vm().error).toMatch(/short name/);
  await act(async () => vm().setSlug("bakery"));
  await act(async () => vm().submit());
  expect(net.sent[0]!.body).toEqual({ name: "Bakery", slug: "bakery" });
});

it("says a taken short name and stays where it was", async () => {
  net.answers.set("/v1/orgs", () => Promise.reject(new ApiError(409, "conflict", "org slug 'bakery' is taken", "r1")));
  await mount();
  await act(async () => vm().setName("Bakery"));
  await act(async () => vm().setSlug("bakery"));
  await act(async () => vm().submit());
  expect(vm().error).toBe("Org slug 'bakery' is taken.");
  expect(net.sent.map((s) => s.path)).toEqual(["/v1/orgs"]);
  expect(useSessionStore.getState().token).toBe("ses_home");
  expect(net.navigate).not.toHaveBeenCalled();
});
