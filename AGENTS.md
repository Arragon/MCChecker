# Agent Instructions

Standing instructions for agents working in this repository. Red lines and scenario rules remain in `CLAUDE.md` and `/agent/docs/*`.

## UI verification

Every user-visible interface in this repository is verified with Playwright. This includes pages, dialogs, components, layout, and interaction, on every screen, not only the one just edited.

- Drive the changed flow in a browser: open, click, type, submit, and assert the outcome. A single screenshot is not verification.
- Use `app/web/template.html` when the change is frontend-only. Use the running server when the change depends on the backend.
- Keep API and non-UI checks on the existing pytest suites. Playwright does not replace them.
- Do not use the computer-use desktop agent for browser UI. Use it only when the surface is not a browser, or when the user explicitly asks for it. Say which one was used.
- Playwright is a local test tool. Do not add it to the application's runtime dependencies.
