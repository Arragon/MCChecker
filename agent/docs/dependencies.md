# Environment and Dependency Management Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Adding, deleting, upgrading, or downgrading third-party dependencies
- Modifying Python, Node.js, or other runtime version requirements
- Modifying system dependencies, database, cache, middleware, or external service requirements
- Modifying installation methods, build tools, package managers, or lock files
- Modifying dependencies required for running tests
- Creating or maintaining `/requirements.md`, `/requirements.txt`

---

## 2. Basic Principles

1. Dependencies should be as few, stable, and necessary as possible, and consistent with the existing tech stack.
2. Prioritize use of standard libraries, existing dependencies, and existing project capabilities.
3. Before adding new dependencies, necessity, compatibility, security risks, and maintenance costs must be confirmed.
4. Do not arbitrarily introduce large frameworks or heavyweight tools for minor problems.
5. Do not upgrade core dependencies or batch update lock files without task basis.
6. Environment and dependency information must be reproducible, verifiable, and traceable.
7. System versions or dependency versions that cannot be confirmed must not be falsified; they should be marked with verification status or confirmation requested.

---

## 3. Dependency Files That Must Be Maintained

### 3.1 Environment Description File

Runnable software projects must maintain:
`/requirements.md`
This file is used to record complete environment and installation requirements.

### 3.2 Python Dependency File

If the project uses Python, it must maintain:
`/requirements.txt`
Requirements:
- Python third-party packages that the project run, test, or toolchain explicitly depends on must be recorded.
- Dependencies with confirmed fixed versions should use explicit versions as much as possible, e.g.:
  flask==3.0.0
  pytest==8.2.0
  requests==2.32.3
- Do not add `import` in code and omit corresponding installation dependencies.
- Do not just write dependencies in documentation without maintaining real dependency files.

### 3.3 Frontend or Node.js Dependency File

If the project uses Node.js, maintain according to project status:
- `/package.json`
- `/package-lock.json`
- `/yarn.lock`
- `/pnpm-lock.yaml`
Do not manually create lock file content inconsistent with the package manager.

### 3.4 Other Dependency Declarations

Maintain as needed according to the project's actual tech stack:
- `pyproject.toml`
- `Pipfile` / `Pipfile.lock`
- `poetry.lock`
- `Dockerfile`
- `docker-compose.yml`
- `go.mod` / `go.sum`
- `Cargo.toml` / `Cargo.lock`
- `Gemfile` / `Gemfile.lock`
- System installation scripts or deployment manifests

---

## 4. Mandatory Content for `/requirements.md`

`/requirements.md` should at least include the following structure, filled with actual project information:

```md
# Requirements

## 1. Supported Systems
| Item | Requirement | Verification Status |
| --- | --- | --- |
| OS | Windows / Linux / macOS | Verified / Pending |
| OS Version | e.g., Windows 11, Ubuntu 22.04 | Verified / Pending |
| Architecture | x86_64 / arm64 | Verified / Pending |

## 2. Runtime Versions
| Runtime | Version | Purpose |
| --- | --- | --- |
| Python | x.x.x | Backend / scripts / tests |
| Node.js | x.x.x | Frontend / build |
| npm / pnpm / yarn | x.x.x | Dependency management |

## 3. System Dependencies
| Dependency | Version / Requirement | Purpose |
| --- | --- | --- |
| Database |  |  |
| Redis / Cache |  |  |
| Browser / Driver |  |  |
| Compiler / System Library |  |  |
| External Service |  |  |

## 4. Application Dependencies
- Python dependencies: see `requirements.txt`
- Frontend dependencies: see `package.json` and lock file
- Other dependencies: list actual files used by this project

## 5. Environment Variables
| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| EXAMPLE_VAR | Yes / No | Description | safe-example |

> Do not record real passwords, tokens, secrets or production connection strings.

## 6. Installation
Provide reproducible installation commands.

## 7. Run
Provide development and production run commands where applicable.

## 8. Test
Provide verified test commands and prerequisites.

## 9. Compatibility and Notes
Record platform limitations, required services and known environment issues.
```

---

## 5. Dependency Change Flow

When involving dependency changes, process according to the following flow:

1. Confirm existing dependencies and package management methods.
2. Determine if existing dependencies or standard libraries can be reused.
3. Explain reasons for adding, deleting, or adjusting dependencies.
4. Update dependency declarations and lock files using the project's existing package management method.
5. Update `/requirements.md`.
6. If Python dependency changes, update `/requirements.txt`.
7. If installation, startup, configuration, or deployment is affected, update:
   - `/docs/manual.md`
   - `/README.md` or `/readme.md`
8. Execute relevant installation, build, startup, or test verification.
9. List dependency changes and verification results in the final response.

---

## 6. Dependency Change Review Checklist

Before adding or upgrading dependencies, confirm:

- Is the dependency really needed?
- Is it compatible with the current runtime and operating system?
- Is it compatible with project license and security requirements?
- Is it in a maintained state?
- Are there obvious known security risks?
- Does it significantly increase installation, build, or deployment complexity?
- Does it require extra system libraries, external services, or environment variables?
- Will it change production images, package size, or startup process?

---

## 7. Prohibited Behaviors

- Do not just modify code and omit dependency declarations.
- Do not just modify dependency declarations without verifying affected features.
- Do not batch upgrade dependencies without knowing the impact scope.
- Do not submit real credentials or sensitive production configurations.
- Do not write a specific system or version as "supported" without verification.
