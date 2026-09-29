// Pure: what a new team org asks for, the short name it suggests, and what a
// refusal says. No React, no fetch. The name is required; the short name is
// optional, and when it is left empty the server makes one from the name.
import { errorMessage } from "../../app/errorMessage";

export const MAX_SLUG_LENGTH = 48;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export interface NewOrgForm {
  name: string;
  slug: string;
}

export interface NewOrgCheck {
  ok: boolean;
  message: string | null;
}

export function checkNewOrg(form: NewOrgForm): NewOrgCheck {
  if (!form.name.trim()) return { ok: false, message: "Name the organization." };
  if (form.slug && !isSlug(form.slug))
    return {
      ok: false,
      message: "The short name is lower-case letters and digits joined by hyphens.",
    };
  return { ok: true, message: null };
}

export function isSlug(slug: string): boolean {
  return slug.length <= MAX_SLUG_LENGTH && SLUG.test(slug);
}

/** The short name suggested for an org's name: lower-case ASCII letters and
 * digits, every other run as one hyphen, trimmed. The server adds a tail to
 * the name's own when the person leaves the field empty. */
export function suggestSlug(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/-+$/g, "");
}

/** What the request carries: the name trimmed, and the slug only when one was typed. */
export function newOrgBody(form: NewOrgForm): { name: string; slug?: string } {
  const name = form.name.trim();
  return form.slug ? { name, slug: form.slug } : { name };
}

export function newOrgRefusal(caught: unknown): string {
  return errorMessage(caught, "Creating the organization failed.");
}
