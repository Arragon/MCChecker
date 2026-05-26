# Startup, Configuration, Deployment, and O&M Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Project start, stop, or restart methods
- Command line parameters, configuration files, or environment variables
- Development, test, or production environment running methods
- Docker, service processes, reverse proxies, scheduled tasks, or background tasks
- Deployment, upgrade, rollback, backup, or recovery flows
- O&M troubleshooting and health checks

Detailed project operation instructions should be maintained in:
`/docs/manual.md`

---

## 2. Basic Principles

1. Running and deployment instructions must come from actual executable methods; do not fabricate commands out of thin air.
2. Modifications to start, stop, configuration, and deployment must consider compatibility with existing users and environments.
3. Default configurations should be as safe as possible; sensitive configurations must be injected through secure methods.
4. Dangerous O&M operations must explain risks, preconditions, and recovery methods.
5. After changes in commands, parameters, ports, paths, or environment variables, documentation must be synchronized.

---

## 3. Startup and Stop Requirements

When adding or modifying startup methods, confirm and record:

- Development environment startup method
- Production environment startup method, if provided by the project
- Required prerequisite services, e.g., database, Redis, browser driver, or external APIs
- Default listening address and port, if suitable for public recording
- Judgment method for successful startup
- Method for viewing logs or status

When a project can be started, `/docs/manual.md` must state corresponding stop methods, e.g.:

- How to terminate foreground processes
- How to stop background processes
- How to stop Docker / Compose services
- How to stop system services
- Whether temporary resources need to be cleaned up

Do not just record startup methods and omit stop methods.

---

## 4. Configuration and Parameter Requirements

When involving parameter or configuration changes, record:

- Configuration file path
- Environment variable name
- Command line parameter name
- Parameter purpose
- Whether it is required
- Default or safe example value
- When it takes effect after modification
- Whether a restart is required
- Possible impact of incorrect configuration

Security requirements:

- Only write variable names and safe examples in documentation.
- Do not record real passwords, tokens, keys, cookies, or production connection strings.
- Do not hardcode keys into source code, frontend resources, or images.

---

## 5. Deployment Requirements

When modifying deployment methods, evaluate and record as needed:

- Deployment preconditions
- Build steps
- Configuration injection method
- Database migration method
- Static resource handling method
- Startup and health check method
- Log viewing method
- Upgrade steps
- Rollback or recovery method
- Downtime or compatibility impact

Deployment-related changes usually also require updating:

- `/requirements.md`
- `/docs/manual.md`
- `/README.md` or `/readme.md`
- `/docs/architecture.md`, if deployment structure changes

---

## 6. O&M Operation Safety

The following operations are high-risk; risks must be clearly explained before execution or suggestion:

- Deleting databases, tables, fields, or files
- Overwriting production configurations
- Batch migration or batch modification of data
- Clearing caches, buckets, task queues, or user content
- Modifying permissions, certificates, domains, or network exposure methods
- Stopping production services
- Version upgrade without rollback plan

If the task does not require direct execution of high-risk operations, prioritize providing steps, checkpoints, and rollback suggestions instead of automatic execution.

---

## 7. Verification Requirements

After O&M or deployment-related modifications, verify according to scenario:

- Whether installation steps still hold
- Whether configuration can be correctly loaded
- Whether startup commands are effective
- Whether stop or restart methods are clear
- Whether health check or basic access is successful
- Whether obvious abnormalities exist in logs
- Whether deployment documents are consistent with real commands

If actual startup or deployment is impossible, unverified content and user verification steps must be stated.
