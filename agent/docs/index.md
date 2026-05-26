# Agent Detailed Rule Index

## 1. Purpose

This directory is used to store detailed rules that need to be loaded by scenario when the Agent performs software development tasks.

Note the distinction:

- `/agent/rules.md`: Global constraints and red lines.
- `/agent/docs/*.md`: Agent execution rules, not project business documents.
- `/docs/architecture.md`: Actual project architecture description.
- `/docs/manual.md`: Project user and O&M manual.
- `/docs/know-how.md`: Project experience, problems, and troubleshooting records.
- `/requirements.md`: Project environment and dependency description.
- `/requirements.txt`: Python dependency file.
- `/README.md` or `/readme.md`: Project overview document.

Do not directly fill in examples or templates from rule files as project facts; project document content must come from actual code, configuration, testing, or confirmed requirements.

---

## 2. Basic Flow for All Tasks

When processing repository tasks, execute in the following order:

1. Read `/agent/rules.md` and this file.
2. Determine which types the task belongs to.
3. Load corresponding detailed rules according to the task type; load all relevant rules for composite tasks.
4. Read project code, configuration, tests, and existing project documents related to the task.
5. Clarify objectives, impact scope, risks, verification methods, and documents that need to be synchronized.
6. Complete the task using minimal viable modifications.
7. Execute relevant tests, static checks, builds, or manual verification.
8. Update affected project documents and dependency files.
9. Check the final diff to avoid unrelated modifications, sensitive information, and missing files.
10. Report results according to the specified format.

---

## 3. Scenario and Rule Loading Table

| Task Scenario | Mandatory Rules to Load | Common Additional Rules |
| --- | --- | --- |
| Analyzing code, locating issues, no file modification | `core.md` | `architecture.md`, `knowledge.md` |
| Adding features | `core.md`, `testing.md` | `architecture.md`, `documentation.md`, `security.md` |
| Fixing bugs | `core.md`, `testing.md` | `knowledge.md`, relevant domain rules |
| Refactoring code | `core.md`, `testing.md`, `architecture.md` | `documentation.md` |
| Modifying API, service interface, or module boundaries | `core.md`, `architecture.md`, `testing.md` | `security.md`, `documentation.md` |
| Modifying pages, components, CSS, or interactions | `core.md`, `frontend.md`, `testing.md` | `documentation.md` |
| Adding/deleting or upgrading dependencies | `core.md`, `dependencies.md`, `testing.md` | `operations.md`, `documentation.md` |
| Modifying startup commands, env variables, or config items | `core.md`, `operations.md`, `documentation.md` | `dependencies.md`, `security.md` |
| Modifying deployment, containers, service config, or CI/CD | `core.md`, `operations.md`, `testing.md` | `dependencies.md`, `documentation.md`, `security.md` |
| Modifying login, permissions, input validation, file upload, external calls | `core.md`, `security.md`, `testing.md` | `architecture.md`, `documentation.md` |
| Modifying database, ORM, SQL, or migration scripts | `core.md`, `database.md`, `testing.md` | `architecture.md`, `operations.md`, `security.md` |
| Updating README, manual, or project documents | `documentation.md` | Domain rules corresponding to document content |
| Troubleshooting complex issues and distilling experience | `core.md`, `knowledge.md` | `testing.md`, relevant domain rules |

---

## 4. Project Documentation Maintenance Judgment

The following files are project documents; they are not required to be modified indiscriminately for every task, but must be synchronized when relevant changes occur:

| Project File | When to Read | When to Update |
| --- | --- | --- |
| `/docs/architecture.md` | Before modifying structure, interfaces, modules, or data flow | After changes in architecture, module boundaries, data flow, interfaces, build, or deployment structure |
| `/docs/manual.md` | Before modifying start, stop, configuration, usage, or maintenance methods | After changes in user operation methods, parameters, start/stop, or deployment/maintenance methods |
| `/docs/know-how.md` | Before troubleshooting known issues, complex bugs, or special constraints | After forming reusable experience, troubleshooting solutions, or important precautions |
| `/requirements.md` | Before modifying environment, dependencies, installation, start, or test methods | After changes in environment, dependencies, installation, run, or test requirements |
| `/requirements.txt` | Before Python project installation, testing, or dependency adjustment | After changes in Python third-party dependencies |
| `/README.md` or `/readme.md` | Before understanding project entry, quick start, features, and deployment | After changes in feature highlights, architecture overview, quick start, deployment, or test entry |

For runnable software projects, if the above necessary project documents are missing, the Agent should create corresponding files based on verified facts in the first task involving relevant domains.

---

## 5. Composite Task Loading Examples

### Example A: Adding a management page with permission control

Need to load:

- `core.md`
- `frontend.md`
- `testing.md`
- `security.md`
- `documentation.md`

If also adding API or adjusting module structure, additionally load:

- `architecture.md`

---

### Example B: Adding Python library and modifying startup parameters

Need to load:

- `core.md`
- `dependencies.md`
- `operations.md`
- `testing.md`
- `documentation.md`

Must check and update as needed:

- `/requirements.txt`
- `/requirements.md`
- `/docs/manual.md`
- `/README.md` or `/readme.md`

---

### Example C: Modifying database fields and migrating existing data

Need to load:

- `core.md`
- `database.md`
- `testing.md`
- `architecture.md`

If deployment or rollback process changes, additionally load:

- `operations.md`
- `documentation.md`

---

## 6. Handling Conflicts and Uncertainty

1. When detailed rules conflict, choose the safer, more conservative, and easier-to-verify approach.
2. When existing project conventions conflict with templates, prioritize maintaining verified project conventions and explain why.
3. When versions, commands, deployment methods, or business behaviors cannot be confirmed, do not guess as facts:
   - Prioritize confirmation from code, configuration, CI, lock files, and existing documents;
   - If still unconfirmed, clearly mark as pending or ask the user.
4. When a task may involve deleting data, breaking compatibility, modifying production configuration, or leakage risk, explain the risk first, then obtain explicit authorization or provide a safe alternative.
