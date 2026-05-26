# General Development Rules

## 1. Applicability

This file must be loaded when the task involves any of the following:

- Modifying source code
- Modifying configurations, scripts, templates, or build files
- Modifying tests
- Fixing bugs
- Implementing refactoring
- Reviewing existing implementations and proposing executable modifications

---

## 2. Pre-modification Check

Before implementation, checks matching the task must be completed:

1. View relevant directories, entry files, call relationships, and configuration sources.
2. Look for existing implementations, public tools, components, styles, tests, and similar features; prioritize reuse.
3. View relevant project documents, e.g.:
   - `/README.md` or `/readme.md`
   - `/docs/architecture.md`
   - `/docs/manual.md`
   - `/docs/know-how.md`
   - `/requirements.md`
4. Check version control status or existing differences to avoid overwriting uncommitted user modifications.
5. Confirm task:
   - Input and output
   - Success criteria
   - Affected modules
   - Compatibility risks
   - Testing methods
   - Documentation and dependency impact

If task information is insufficient for safe implementation, raise questions or clarify adopted assumptions first.

---

## 3. Implementation Principles

### 3.1 Minimal Change

- Only modify files and logic necessary to complete the current task.
- Avoid unrelated renaming, directory adjustments, full formatting, or dependency upgrades.
- Bug fixes prioritize root cause location; do not "pass" by hiding errors or deleting validations.

### 3.2 Reuse Existing Design

- Prioritize reuse of existing project modules, functions, components, styles, configuration patterns, and error handling methods.
- Do not re-implement existing capabilities.
- New implementations should naturally integrate into existing directories, naming, and call relationships.

### 3.3 Compatibility

Unless explicitly required, do not change:

- External API request or response formats
- Configuration key names and default behaviors
- Meaning of data fields
- Command line parameter semantics
- User operation flows
- Persistent data structures
- Public function or component interfaces

When changes are necessary:

1. Explain changes and reasons.
2. Evaluate affected callers and migration costs.
3. Provide compatibility layers, migration plans, or deprecation notices as much as possible.
4. Update tests and relevant documentation.

---

## 4. Code Quality

### 4.1 Readability

Code should:

- Accurately express intent through naming.
- Have single-responsibility functions and modules.
- Have a clear logical structure, avoiding excessive nesting.
- Have necessary comments for complex judgments, compatibility strategies, or business rules.
- Explain "why" in comments, avoiding restating obvious code.

### 4.2 Error Handling

- Handle failure scenarios for external inputs, network requests, file operations, database operations, and third-party service calls.
- Do not swallow exceptions; if converting exceptions, retain sufficient context information.
- Error messages for users should be clear but not leak internal sensitive details.
- Recoverable errors should provide clear feedback or recovery paths.

### 4.3 Logging

Suitable locations for logging include:

- Core task start, completion, and failure
- External service call failure
- State transitions and important business branches
- Exception recovery and degradation paths

Logging requirements:

- Use existing project logging facilities.
- Log content should facilitate problem location.
- Do not log passwords, tokens, keys, full connection strings, session content, or sensitive user data.
- Avoid meaningless high-frequency log pollution.

---

## 5. File and Command Operation Safety

- Do not delete unknown files, user files, database data, or uncommitted changes.
- Do not execute irreversible commands unless the task explicitly requires it and risks have been explained.
- Do not overwrite `.env`, production configurations, key files, or user local configurations.
- Generated files, build artifacts, or formatting results should be limited to the necessary range.
- If tools produce large amounts of unrelated diff, clean up before delivery.

---

## 6. Post-modification Check

After completing modifications, check:

1. Whether changes only cover the task scope.
2. Whether unused code, dead code, debug output, or temporary files are introduced.
3. Whether there are obvious error handling omissions.
4. Whether tests need to be added or updated.
5. Whether dependencies, configuration, startup, deployment, database, security, or documentation are affected.
6. Whether other rules need to be loaded and processed further.
7. Whether sensitive information or unrelated changes exist in the final diff.

---

## 7. Delivery Information

Final response should at least state:

- What modifications were made.
- Why this implementation method was adopted.
- What verifications were performed and their results.
- Whether dependencies, configuration, data structures, or user operation methods were changed.
- What documents were updated.
- What risks, limitations, or items needing manual confirmation remain.
