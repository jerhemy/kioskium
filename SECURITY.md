# Security policy

## Supported versions

Only the latest release receives fixes. Kioskium bundles Qt WebEngine (Chromium); Chromium security
fixes arrive through new PySide6 releases, so keep up with Kioskium releases on devices that load
untrusted content.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository
([GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).

Include the version (`kiosk --version`), the platform, the flags you run with, and the steps to
reproduce. You will get an answer within a week; fixes are released as soon as they are ready and
credited in the release notes unless you prefer otherwise.

## Things that are by design

- The **control API** (`--control-port`) listens on `127.0.0.1` only and has no authentication
  unless you set `--control-token`. Any local process can drive the browser; use a token, or the
  Unix socket (`--control-socket`, owner-only permissions) on shared systems.
- Chromium's renderer sandbox is off by default (it refuses to run as root and needs user
  namespaces, which embedded targets often lack); turn it on with `--sandbox` where it works,
  especially for content that is not your own.
- `--allow-file-remote-access`, `--ignore-certificate-errors` and `--remote-debugging-port`
  weaken Chromium's protections. They exist for embedded targets and development; don't use them
  with untrusted content.
- `window.kioskium` (the page JavaScript API) can open DevTools and split the screen. Disable it with
  `--no-js-api` when the page is not your own.
