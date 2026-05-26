# Project Documentation Maintenance Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Project feature highlight changes
- User-visible behavior changes
- Start, stop, configuration, parameter, or operation flow changes
- Installation, deployment, test, or environment requirement changes
- Architecture, module, interface, or data flow changes
- Adding reusable experience, FAQs, or important precautions
- Creating, supplementing, or correcting documentation

---

## 2. Documentation System

Projects should maintain the following documents as needed:

| File | Purpose |
| --- | --- |
| `/README.md` or `/readme.md` | Project overview, feature highlights, architecture intro, quick start, deployment entry |
| `/docs/manual.md` | Detailed user and maintenance manual, including start/stop, config, usage, troubleshooting, and notes |
| `/docs/architecture.md` | Tech architecture, directory structure, core modules, data flow, and important decisions |
| `/docs/know-how.md` | Verified experience, FAQs, debugging solutions, and special constraints |
| `/requirements.md` | System, runtime, dependency, installation, run, and test environment description |
| `/requirements.txt` | Python third-party dependency declaration, applicable to Python projects |

Documentation must be consistent with actual implementation. Do not fill in unconfirmed features, commands, versions, or deployment methods just to "complete documentation."

---

## 3. README Maintenance Rules

### 3.1 Filename

- If `/README.md` or `/readme.md` already exists, maintain the existing naming style.
- If neither exists, create `/README.md`.
- Two README files with duplicate content but different cases should not be maintained simultaneously.

### 3.2 README Positioning

README is for users and developers first contacting the project; it should provide overview and entry, and should not pile up excessively long operation details. Put detailed operations into `/docs/manual.md`.

### 3.3 Mandatory Content for README

README should at least cover:

```md
# Project Name

## Overview
Briefly describe problem solved, target users, and main use.

## Features
- Main feature highlights
- Core tasks users can complete
- Important capabilities or advantages

## Architecture
Briefly describe overall architecture, main modules, and running mode.
See `docs/architecture.md` for detailed architecture.

## Tech Stack
- Backend:
- Frontend:
- Database / Storage:
- Deployment / Runtime:

## Requirements
State main running requirements and point to `requirements.md`.

## Quick Start
Provide shortest viable installation and startup flow.
See `docs/manual.md` for detailed operations.

## Deployment
Provide deployment overview, key steps, and detailed documentation entry.

## Testing
List basic test commands or test entries.

## Documentation
- `docs/manual.md`: Detailed use and O&M manual
- `docs/architecture.md`: Project architecture
- `docs/know-how.md`: Experience and troubleshooting records
- `requirements.md`: Environment and dependency description

## Notes
Important limitations, safety tips, or compatibility instructions.
```

### 3.4 Scenarios Where README Must Be Updated

- Core feature highlight addition, deletion, or obvious change
- Tech stack or architecture overview change
- Quick start path change
- Deployment method change
- Test entry change
- Main document entry change
- Change in important items users must know for first use

---

## 4. User Manual Maintenance Rules

### 4.1 Mandatory Files to Maintain

Projects must maintain:
`/docs/manual.md`
This file is used to detail how users and maintainers start, stop, configure, use, and troubleshoot the project.

### 4.2 Mandatory Content for `/docs/manual.md`

```md
# User Manual

## 1. Overview
Explain project purpose, application scenarios, user roles, and usage prerequisites.

## 2. Prerequisites
Explain system, runtime, dependencies, services, and configuration required before use.
Detailed dependency information can point to `requirements.md`.

## 3. Installation
Explain installation steps, dependency installation commands, and initialization flow.

## 4. Start Project
Explain applicable startup methods respectively, e.g.:
- Development environment startup
- Production environment startup
- Docker / Compose startup
- Background service startup

Should explain startup preconditions, commands, default access methods, and startup success judgment methods.

## 5. Stop Project
Detail how to safely end the project, e.g.:
- Foreground process stop method
- Background process stop method
- Docker / Compose stop method
- System service stop method
- Whether temporary resources need to be cleaned up

## 6. Configuration
Explain how to modify project parameters, including:
- Configuration file location
- Environment variable name and purpose
- Command line parameters
- Default or safe example
- Whether restart is needed after modification
- Common configuration examples

Do not write real keys, tokens, or production passwords.

## 7. Common Operations
Explain common user or maintenance operation steps.

## 8. Deployment and Maintenance
Explain deployment, upgrade, log viewing, health checks, backup, or recovery methods.

## 9. Troubleshooting
List common problems, symptoms, causes, and resolution steps.

## 10. Notes and Safety
Explain usage limitations, data security, permissions, compatibility, and high-risk operation precautions.
```

### 4.3 Scenarios Where User Manual Must Be Updated

- Startup command, stop method, or access address change
- Configuration file, environment variable, command parameter change
- User operation flow change
- Deployment, upgrade, maintenance, or troubleshooting method change
- Addition of important precautions, safety tips, or compatibility limitations

---

## 5. Architecture Document Maintenance Rules

Update the following when architecture, directory, module responsibility, interface, data flow, state management, build, test, or deployment structure change:
`/docs/architecture.md`
Specific requirements see:
`/agent/docs/architecture.md`

---

## 6. Experience Document Maintenance Rules

Update the following when resolving complex problems, confirming special limitations, or forming reusable troubleshooting experience:
`/docs/know-how.md`
Specific requirements see:
`/agent/docs/knowledge.md`

---

## 7. Environment and Dependency Document Maintenance Rules

Update the following when dependencies, system, runtime, installation, startup, testing, or environment variables change:
`/requirements.md`
If involving Python third-party dependency changes, also update:
`/requirements.txt`
Specific requirements see:
`/agent/docs/dependencies.md`

---

## 8. Documentation Update Matrix

| Change Type | Documents to Consider Updating |
| --- | --- |
| New user feature | README, manual, architecture if needed |
| Modify user operation flow | manual, README if needed |
| Modify start or stop method | manual, requirements, README if needed |
| Modify config or env variables | manual, requirements, README if needed |
| Modify dependencies or runtime | requirements, corresponding dependency file, manual/README if needed |
| Modify architecture or data flow | architecture, README if needed |
| Modify deployment method | README, manual, requirements, architecture if needed |
| Modify test command | requirements, README or manual |
| Resolve complex issue and form experience | know-how |
| Modify DB structure or migration | architecture, manual or requirements, depending on impact |

---

## 9. Documentation Quality Requirements

- Commands in documentation should be as copyable and executable as possible.
- Paths, ports, and parameter names in documentation should be consistent with code and configuration.
- Documentation should distinguish between development, test, and production environments.
- Example configurations must be safe and not contain real secrets.
- Do not record unverified support scope; mark clearly when unconfirmed.
- Keep README concise, put detailed flows into manual or specific documents.
- After document modification, check links, filename case, and whether commands are correct.
