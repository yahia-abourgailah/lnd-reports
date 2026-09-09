/**
 * WCAG 2.1 AA, on every screen, against the running application.
 *
 *   npm run a11y            # dev stack must be up: make up
 *
 * Week 10, Person B. The accessibility pass was promised in week 5 and deferred
 * every week since, which is the usual shape: it is the check that never blocks
 * anything until it does. Running it against the real application rather than
 * against rendered components is deliberate — colour contrast is the rule this
 * codebase is most likely to break, and it cannot be evaluated without layout
 * and computed styles.
 *
 * WHAT IS CHECKED
 *
 * Every route, including the five added since week 7, in both themes. Serious
 * and critical violations fail; moderate and minor are reported and do not,
 * because a suite that fails on a decorative landmark teaches everybody to run
 * it with a flag.
 *
 * WHY THE DRILL DRAWER AND THE DEFINITION PANEL ARE OPENED
 *
 * They are the two places in this application where content appears without a
 * navigation, and both are where a keyboard user is most likely to be stranded.
 * A route-level scan never sees either of them, and would report a clean pass
 * on a dialog nobody can leave.
 */

import { AxeBuilder } from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const ROUTES = [
  ['Overview', '/'],
  ['Coverage', '/coverage'],
  ['Funnel', '/funnel'],
  ['Programmes', '/programs'],
  ['Trainers', '/trainers'],
  ['Learners', '/learners'],
  ['Reports', '/reports'],
  ['Exceptions', '/exceptions'],
  ['Enrichment', '/enrichment'],
] as const;

/** The tags that make up WCAG 2.1 AA, named rather than inherited by default. */
const WCAG_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

/** A violation worth failing a build over. */
const BLOCKING = new Set(['serious', 'critical']);

async function scan(page: import('@playwright/test').Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_AA).analyze();
  const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''));
  const advisory = results.violations.filter((v) => !BLOCKING.has(v.impact ?? ''));
  if (advisory.length) {
    // Reported, never failed on. See the header: a suite that fails on a
    // decorative landmark is a suite everybody runs with a flag.
    console.log(
      'advisory:',
      advisory.map((v) => `${v.id} (${v.impact}, ${v.nodes.length})`).join(', '),
    );
  }
  return blocking;
}

function describeViolations(violations: Awaited<ReturnType<typeof scan>>) {
  return violations
    .map(
      (v) =>
        `${v.id} — ${v.help}\n    ${v.nodes
          .slice(0, 3)
          .map((n) => n.target.join(' '))
          .join('\n    ')}`,
    )
    .join('\n  ');
}

test.beforeEach(async ({ page }) => {
  // Dev bypass issues the session without an IdP. The app redirects to login
  // when there is none, and every scan would then be a scan of the login page —
  // which passes, and proves nothing about the ten screens behind it.
  await page.goto('/v1/auth/login');
  await page.waitForURL((url) => !url.pathname.startsWith('/v1/auth'));
});

for (const [name, path] of ROUTES) {
  test(`${name} has no serious or critical violations`, async ({ page }) => {
    await page.goto(path);
    // The figures arrive over the network; scanning before they land is
    // scanning a page of skeletons, which is not the page anybody uses.
    await page.waitForLoadState('networkidle');
    const violations = await scan(page);
    expect(violations, `${name}:\n  ${describeViolations(violations)}`).toEqual([]);
  });
}

/**
 * Every screen is reachable from the navigation, and arrives with content.
 *
 * Run this against the built bundle as well as the dev server:
 *
 *   npm run build && npm run preview
 *   A11Y_BASE_URL=http://localhost:4173 npm run a11y
 *
 * A route that works under Vite and 404s from nginx is a real failure mode, and
 * so is a screen that renders its empty state because the API call it needed
 * was never made. Both look like a working application from a screenshot.
 */
test('every screen is reachable from the navigation', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  for (const [name, path] of ROUTES) {
    const link = page.getByRole('link', { name, exact: true });
    await expect(link, `no navigation link for ${name}`).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${path.replace('/', '\\/')}(\\?|$)`));
    await page.waitForLoadState('networkidle');

    // Something rendered under the chrome. Not an assertion about *what* —
    // that is the suite's job — but a page whose main region is empty is a
    // route that resolved to nothing.
    const main = page.getByRole('main');
    await expect(main).not.toBeEmpty();
  }
});

test('the drill drawer is reachable and leaveable', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  // The number itself is the control — a KPI's value is a button whose title
  // says how many rows are behind it.
  await page.locator('button.kpi-value:not([disabled])').first().click();

  const drawer = page.getByRole('dialog');
  await expect(drawer).toBeVisible();

  const violations = await scan(page);
  expect(violations, `drill drawer:\n  ${describeViolations(violations)}`).toEqual([]);

  // Escape closes it. A drawer a keyboard user can open and not leave is worse
  // than one they cannot open.
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
});

test('a metric definition panel is announced', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  const definition = page.getByRole('button', { name: /what does .* mean\?/i }).first();
  await definition.click();
  await expect(page.getByRole('region', { name: /definition$/i }).first()).toBeVisible();

  const violations = await scan(page);
  expect(violations, `definition panel:\n  ${describeViolations(violations)}`).toEqual([]);
});

/**
 * The dashboard has one appearance now, and this is what asserts it.
 *
 * It was written against a dark theme that has since been removed: the platform
 * carries the workbook's own navy and stays light whatever the machine is set
 * to, so that two people looking at one figure are not describing
 * different-looking screens. Emulating dark should therefore change nothing —
 * and the ground actually being painted is the thing worth checking, because a
 * page that leaves `body` transparent inherits the host's colour and puts one
 * theme's text on the other theme's background.
 */
test('the dashboard looks the same whatever the machine is set to', async ({ page }) => {
  const grounds = new Set<string>();

  for (const scheme of ['dark', 'light'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    grounds.add(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
    const violations = await scan(page);
    expect(violations, `${scheme}:\n  ${describeViolations(violations)}`).toEqual([]);
  }

  expect(grounds.size, `the ground changed with the OS theme: ${[...grounds]}`).toBe(1);
  expect([...grounds][0]).not.toBe('rgba(0, 0, 0, 0)');
});
