# Acme company site

The public page at `https://acme.example` (and `https://staging.acme.example`
on staging): what Acme is, what it does today, the plans, and the way into
the app. One page, plus the page a missing address gets.

## What it is made of

HTML and CSS, built by Vite, and no script. That is a choice. A page that
says what a product is needs no framework, and a page with no script is
fast, has nothing to break, and runs under the strictest security policy
the distribution sends. Vite is still worth having: it bundles the font,
hashes every asset for a year of caching, copies the demo GIF in, and
writes each environment's links into the HTML.

- `index.html` is the page; `404.html` is the not-found page.
- `src/site.css` is the look. It imports the portal's own
  `apps/portal/src/design/theme.css`, so the tokens, the indigo accent,
  and the light and dark values are the portal's, not a copy of them.
  Dark follows the system; there is no toggle, since a toggle is a script.
- Inter is bundled from `@fontsource-variable/inter`, the way the portal
  bundles it. The page loads nothing from another origin: no font, no
  script, no tracker, no analytics.
- The product visual is the README's realtime demo,
  `docs/media/realtime-demo.gif`, referenced from `index.html` and
  copied into the build with a hashed name. A dark system gets
  `docs/media/realtime-demo-dark.gif` instead, through the `<picture>`'s
  dark source, so the demo matches the page. `make demo-gif` records the
  light one again, and `make demo-gif-dark` the dark one.
- `public/` holds the mark, `favicon.svg`, and two drawings of it:
  `favicon-32.png` for a browser that takes no SVG icon and
  `apple-touch-icon.png` for a home screen, which
  `scripts/favicons.sh apps/site/public` draws again from the SVG. The
  portal shows the same three.
- `src/links.ts` decides the links: the app's sign-in and sign-up and the
  repository. The names come from `deployment/cloud/environments.json`,
  the one place every deployed name lives. `vite.config.ts` writes them
  into the HTML in place of `%APP_URL%`, `%SITE_URL%`, and
  `%GITHUB_URL%`, and a placeholder nothing fills fails the build.

## Build per environment

The site calls nothing at runtime, so it cannot read a `/config.json` the
way the portal does. Its links are in its HTML instead, and the build
runs once per environment:

```bash
pnpm --filter @acme/site build     # dist/staging and dist/production
pnpm --filter @acme/site dev       # http://localhost:5174, links to the local portal
```

A mode that is not `staging` or `production` (or `development` and
`test`, locally) is refused. `deploy-staging.yml` builds both pages in
one job, keeps the whole `dist/` under `builds/site/<sha>/` in the
artifacts bucket, records its digest as `deployed/site`, and publishes
`dist/staging` to staging. A release publishes `dist/production` from
the same build, replicated into production's account, once its digest
matches what staging recorded. So production runs the page staging
built, with production's links.

## Where it is served

Terraform's `static_site` module, the portal's module called a second
time: a private bucket read only by its CloudFront distribution, with the
certificate the bootstrap root made for its name. The
distribution sends the same security headers as the portal's, with a
`Content-Security-Policy` that names the page's own origin and nothing
else. A missing path gets `404.html` with a 404.
`scripts/deploy_static.sh site` publishes a build: the hashed assets
cached for a year and uploaded when new, the pages revalidated on every
load, and each page whose content changed uploaded and invalidated. A
build whose pages did not change publishes with no invalidation.

The site's name is a CNAME in the Cloudflare zone, DNS only, to the
distribution; the create run writes it, and its certificate's validation
record. The first-time manual, 18a, has the order.

## Checks

`pnpm --filter @acme/site test` holds that each build links to its own
environment's app, that an unknown mode is refused, that neither page
has a script or loads anything from another origin, and that the plans
carry their prices. `make check` runs it with the lint and the type
check; CI builds the site with every other app.
