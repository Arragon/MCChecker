# Agent Global Rules
> This file contains the highest-level project rules for Agents within the repository; `/agent/docs/*` are detailed rules loaded by scenario.

## P0 Red Lines
1. No Falsification: Do not claim content as completed or passed if it hasn't been executed, verified, or passed.
2. No Leakage: Do not output, submit, or record sensitive information such as passwords, tokens, keys, connection strings, or private data.
3. No Blind Changes: Understand relevant code, configurations, documentation, tests, and impact scope before making modifications.
4. No Destruction: Do not break compatibility, delete data, overwrite user configurations, or perform high-risk operations without explicit request.
5. No Bypassing Tests: Add/update tests and perform relevant verification for new or modified behaviors; explain if execution is impossible.
6. No Arbitrary Dependencies: Provide reasons for adding, deleting, upgrading, or downgrading dependencies and sync dependency files and documentation.
7. No Scope Expansion: Do not include unrelated refactoring, formatting, dependency upgrades, or behavioral changes in the task.

## Priority and Execution Flow
8. Rule Priority: System/Developer Instructions > Red Lines in this file > User Task Requirements > Detailed Rules > Project Documents and Conventions.
9. For each repository task, first read `/agent/docs/index.md` to identify the task type and load necessary rules.
10. Check current status and impact before modification; prioritize minimal, incremental, reversible, and compatible solutions.
11. Synchronize considerations for code, configuration, testing, documentation, dependencies, security, data, and deployment during modification.
12. Perform feasibility verification after modification, check change scope, and report results, risks, and unfinished items as required.

## Detailed Rule Loading Index
13. Modify code, config, scripts, or tests -> Read `/agent/docs/core.md`.
14. Add modules, change interfaces, change data flow, refactor boundaries, or adjust architecture -> Read `/agent/docs/architecture.md`.
15. Page, component, style, interaction, responsive, or accessibility changes -> Read `/agent/docs/frontend.md`.
16. Add/modify features, fix bugs, verify regression, or handle test failures -> Read `/agent/docs/testing.md`.
17. Dependencies, runtime, system environment, installation, or build tool changes -> Read `/agent/docs/dependencies.md`.
18. Start, stop, parameters, environment variables, deployment, or O&M changes -> Read `/agent/docs/operations.md`.
19. User-visible features, usage methods, deployment instructions, or project overview changes -> Read `/agent/docs/documentation.md`.
20. Complex troubleshooting, reusing historical experience, or forming reusable solutions -> Read `/agent/docs/knowledge.md`.
21. Authentication, permissions, input, files, commands, external requests, logs, or sensitive information -> Read `/agent/docs/security.md`.
22. Table structure, migration, queries, transactions, persistence, or data consistency -> Read `/agent/docs/database.md`.

## Delivery Requirements
23. Final response must state: completed content, main changes, dependency changes, test results, documentation updates, risks, or limitations.
24. Explicitly list any unverified content, failed tests, pending environments, or manual operation steps.
