import { defineConfig, devices } from '@playwright/test';

/**
 * The accessibility suite runs against the running dev stack, through the
 * proxy — the same origin a person uses, so `/v1/auth/login` and the SPA are
 * one site and the session cookie applies to both.
 *
 * No `webServer` block: the stack is seven containers with a database behind
 * it, and Playwright starting a Vite server on its own would scan a front end
 * talking to nothing. `make up` first, deliberately.
 */
export default defineConfig({
  testDir: './a11y',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['json', { outputFile: 'a11y-results.json' }]] : 'list',
  use: {
    baseURL: process.env.A11Y_BASE_URL ?? 'http://localhost:8080',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
