// The addresses the page links to, per build mode. The app's and the site's
// public names come from deployment/cloud/environments.json, the one place
// every deployed name lives; a local build points at the portal `make up`
// serves. The page itself calls none of them: each is a link a person follows.

export interface Environments {
  github_repository: string;
  environments: Record<string, { app_domain_name: string; site_domain_name: string }>;
}

export interface Links {
  /** The portal; "Sign in" and "Get started" go there. */
  app: string;
  /** Where this page is served, for its canonical address. */
  site: string;
  /** The public repository. */
  github: string;
}

// A build mode is a deployed environment's name; `vite` runs as `development`
// and `vitest` as `test`, both local.
const deployed = { staging: "staging", production: "production" } as const;

export function linksFor(mode: string, environments: Environments): Links {
  const github = `https://github.com/${environments.github_repository}`;
  if (mode === "development" || mode === "test") {
    return { app: "http://localhost:55173", site: "http://localhost:5174", github };
  }
  const name = deployed[mode as keyof typeof deployed];
  const environment = name === undefined ? undefined : environments.environments[name];
  if (environment === undefined) {
    throw new Error(`the site builds for staging or production (or development and test, locally), not '${mode}'`);
  }
  return {
    app: `https://${environment.app_domain_name}`,
    site: `https://${environment.site_domain_name}`,
    github,
  };
}

/** Fills the page's placeholders; a placeholder left unfilled fails the build. */
export function fillLinks(html: string, links: Links): string {
  const filled = html
    .replaceAll("%APP_URL%", links.app)
    .replaceAll("%SITE_URL%", links.site)
    .replaceAll("%GITHUB_URL%", links.github);
  const left = filled.match(/%[A-Z_]+_URL%/);
  if (left !== null) throw new Error(`the page names ${left[0]}, which no link fills`);
  return filled;
}
