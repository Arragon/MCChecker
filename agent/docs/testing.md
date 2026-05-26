# Testing and Verification Rules

## 1. Applicability

This file must be loaded for the following tasks:

- Adding features
- Modifying existing behaviors
- Fixing bugs
- Refactoring code
- Adjusting dependencies, configuration, deployment, or database
- Modifying security-related logic
- Handling test failures or regression issues

---

## 2. Basic Principles

1. Testing is part of implementation, not an optional finishing task.
2. Modified behavior must have corresponding verification; bug fixes should prioritize adding tests that can reproduce the problem.
3. Tests should verify public behaviors and key constraints, rather than being excessively bound to internal implementation.
4. Do not delete, skip, or loosen tests just to mask failures, unless the original test is indeed invalid and reasons have been explained.
5. Tests that haven't been run must not be reported as passed.

---

## 3. Test Command Search Order

Before executing tests, prioritize confirming the project's actual testing method from the following locations:

1. `/README.md` or `/readme.md`
2. `/docs/manual.md`
3. `/requirements.md`
4. `package.json`
5. `pyproject.toml`
6. `pytest.ini`, `tox.ini`, `setup.cfg`
7. `Makefile`
8. Docker, Compose, or task scripts
9. CI configuration files
10. Existing test directories and naming conventions

Do not assume commands or test environments out of thin air.

---

## 4. Test Scope Selection

### 4.1 New Feature Addition

Cover at least:

- Normal path
- Key boundary conditions
- Necessary exception paths
- Integration points with existing features
- Permission or input security-related scenarios, if applicable

### 4.2 Bug Fix

Complete at least:

1. Confirm problem root cause.
2. Add or update tests that can reproduce the problem.
3. Modify implementation.
4. Verify new tests pass.
5. Execute regression tests for affected areas.

### 4.3 Refactoring

- When behavior is kept unchanged, original relevant tests should be run.
- If tests are insufficient to prove behavior hasn't changed, add tests covering key behaviors.
- Do not claim "pure refactoring with no impact" without verifying behavior.

### 4.4 Dependency, Configuration, and Deployment Changes

Execute according to impact:

- Installation or dependency resolution checks
- Build checks
- Startup checks
- Configuration loading tests
- Regression of related features
- Executability check of document commands

### 4.5 Database Changes

Follow `/agent/docs/database.md` simultaneously and verify:

- Migration executability
- Compatibility of old and new data
- Query and write behaviors
- Rollback or recovery plan, if applicable

---

## 5. Testing Levels

Select appropriate levels based on project actual conditions:

| Test Type | Main Purpose |
| --- | --- |
| Unit Test | Verify functions, classes, components, or pure business logic |
| Integration Test | Verify collaboration between modules, APIs, and storage |
| End-to-End Test | Verify key user flows |
| Build Check | Verify compilation, packaging, types, or static checks |
| Manual Verification | Supplement visual, deployment, or interaction verification that cannot be automated |

Prioritize running most relevant fast tests, then execute broader regression according to impact.

---

## 6. Test Failure Handling

If a test fails:

1. Determine if the failure was caused by the current modification.
2. If caused by the current modification, fix and rerun.
3. If it's an existing failure or environment problem, retain evidence and explain clearly:
   - Failed command
   - Failed test or error summary
   - Judgment on relationship with current modification
   - Completed alternative verification
   - Remaining risks

Do not ignore failures and report the task as completely finished.

---

## 7. When Tests Cannot Be Executed

If tests cannot be executed due to lack of environment, dependencies, services, permissions, credentials, or platform conditions, the final response must state:

- Unexecuted test commands or test scope
- Specific reasons why execution is impossible
- Executed alternative verification, such as static checks, code review, build checks, or local tests
- How users can complete verification in an available environment
- Currently remaining risks

---

## 8. Synchronization of Test-related Documentation

Update the following when test commands, test preconditions, test services, or verification flows change:

- `/requirements.md`
- `/docs/manual.md`, if users or maintainers need to execute the flow
- `/README.md` or `/readme.md`, if quick test entries change
- `/docs/architecture.md`, if testing architecture or key integration methods change
