# Cloudflare Pages + Access migration

## Goal

Deploy the existing static website to Cloudflare Pages and put the complete Pages hostname behind Cloudflare Access. The existing GitHub Pages publication remains enabled until the new site is verified.

## Pages build settings

- Connect the GitHub repository: `mrsjonnie/evenementen`
- Production branch: `main`
- Framework preset: `None` / `None`
- Build command: `bash ./cloudflare-build.sh`
- Build output directory: `public`
- Root directory: repository root (leave blank)
- Do not configure a custom domain yet.
- Do not disable GitHub Pages or its publishing workflow during setup.

The build script deliberately copies only the runtime files into `public/`; it does not publish scraper source code, GitHub workflow files, logs, worker configuration, or feedback data. Runtime JSON files needed by the event page are copied alongside the HTML.

## Cloudflare dashboard steps

1. Sign in at https://dash.cloudflare.com/ and select the account.
2. Open **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
3. Authorize GitHub if prompted and select `mrsjonnie/evenementen`.
4. Enter the build settings above and deploy. Wait for the production deployment to succeed.
5. Open the generated `https://<project-name>.pages.dev` URL and verify the homepage, event page, events list, event filtering, and data refresh flow.
6. In the Pages project, open **Settings** → **Enable access policy**. Use **Manage** to configure the Access application. Restrict the allow policy to your own email address (or another explicitly chosen identity) and choose a one-time PIN or a supported identity provider.
7. Cloudflare's default Pages setup may initially create a wildcard hostname for preview deployments. In **Zero Trust** → **Access controls** → **Applications**, configure the production Access application so its public hostname is the exact `<project-name>.pages.dev` hostname, not the wildcard. Save it. Re-enable/review the preview deployment policy separately if preview deployments are needed.
8. Test in a private/incognito browser: opening both the root URL and `/evenementen.html` must require login. Also try direct paths such as `/events.json` and `/sites.json`; these should require the same Access authentication. After login, confirm both pages and data load.
9. Keep GitHub Pages live during all the above steps. Do not disable its workflow or change repository Pages settings until the protected Pages site has passed the tests above.

## Important security notes

- Cloudflare Access protects the Cloudflare Pages hostname and its assets; it does not protect the existing `github.io` hostname. Therefore GitHub Pages remains a public bypass while it is enabled.
- The event page currently reads scraper progress from the public GitHub raw URL. That status file remains public independently of Access. If scraper progress must also be private, update the page to read it from the protected Pages origin or a protected API before completing the migration.
- The public GitHub repository itself is still public. Access does not make repository source code private.
- The existing Worker/API has its own security and CORS configuration. Access on Pages does not automatically protect the Worker hostname. Review API authorization separately before treating the entire application and all write operations as private.
- Do not store passwords, API tokens, or Cloudflare secrets in the repository.

## Cutover checklist (later, only after successful verification)

- [ ] Protected Pages deployment works for authorized user.
- [ ] Unauthenticated private-window tests are blocked for root, event page, and required JSON assets.
- [ ] Login and refresh/edit workflows have been tested.
- [ ] Worker/API access policy has been reviewed and protected where needed.
- [ ] Only then decide whether to disable GitHub Pages publication. Do not disable it automatically as part of this preparation.
