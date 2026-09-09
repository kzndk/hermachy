const { defineConfig } = require('@playwright/test');
const { existsSync } = require('node:fs');
const executablePath = process.env.HERMACHY_CHROMIUM ||
  (!process.env.CI && existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
module.exports = defineConfig({
  testDir: './tests',
  testMatch: '*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:43129',
    viewport: { width: 1320, height: 860 },
    launchOptions: { executablePath },
  },
  webServer: {
    command: 'python3 -m http.server 43129 --bind 127.0.0.1',
    url: 'http://127.0.0.1:43129',
    reuseExistingServer: false,
  },
  reporter: 'list',
});
