# Architecture and Module Boundary Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Adding, deleting, or splitting modules
- Adjusting directory structures or core responsibility boundaries
- Modifying API, event, message, task, or cross-module call methods
- Modifying state management, caching strategies, or data flows
- Performing refactoring that affects structure
- Modifying build, deployment, or execution architecture
- Needing to create or update project architecture documents

This file is for architecture modification rules. Actual project architecture should be recorded in:
`/docs/architecture.md`

---

## 2. Content to Confirm Before Modification

Before implementing architecture-related modifications, you must:

1. Read existing `/docs/architecture.md`; if it doesn't exist, create it after understanding the actual structure.
2. View relevant entries, modules, routes, services, data models, configurations, and tests.
3. Confirm current module responsibilities, dependency directions, data flows, and external interfaces.
4. Clarify modification motivation:
   - Feature addition
   - Bug root cause fix
   - Performance issues
   - Maintainability issues
   - Security or stability issues
5. Clarify modification impact:
   - Affected modules
   - External interfaces
   - Data structures
   - Configuration and deployment
   - Test scope
   - Compatibility and migration costs

---

## 3. Architecture Modification Principles

### 3.1 Clear Responsibility

- Modules should have clear and stable responsibilities.
- Avoid mixing business logic, database access, UI presentation, and external calls in one location.
- Public capabilities should be extracted when actual reuse needs exist, avoiding over-engineering.

### 3.2 Stable Dependency Direction

- Try to follow the project's existing layering and dependency direction.
- Avoid circular dependencies.
- Avoid lower-level modules reversely depending on specific UI, temporary scripts, or test implementations.
- Boundaries for external systems, databases, file systems, etc., should have clear encapsulation methods.

### 3.3 Stable Interface

- External interface changes should prioritize compatibility with existing callers.
- Changes in request, response, event, configuration, and storage structures should have corresponding tests.
- If there are breaking changes, provide migration instructions or compatible transition solutions.

### 3.4 Keep It Simple

- Prioritize solving currently confirmed problems.
- Do not introduce complex abstractions for requirements that do not yet exist.
- Do not create heavy architectural layers due to minor code duplication.

---

## 4. `/docs/architecture.md` Maintenance Requirements

The project architecture document must be updated when the following changes occur:

- Changes in tech stack or main running components
- Changes in directory structure or core modules
- Changes in backend, frontend, task, service, or data layer responsibilities
- Changes in data flow, call chain, interfaces, or state management
- Changes in database, cache, message system, or external service integration
- Changes in build, test, or deployment architecture
- Changes in important architectural decisions or compatibility strategies

If the file does not exist, create it in the first task involving architecture or core code modification.

---

## 5. Recommended Structure for `/docs/architecture.md`

The Agent should fill it based on actual project conditions and must not retain fictional content.

```md
# Architecture

## 1. Overview
Introduction to project goals, runtime form, and overall architecture.

## 2. Tech Stack
- Backend:
- Frontend:
- Database:
- Infrastructure:
- Testing:

## 3. Directory Structure
Explain main directories and responsibilities.

## 4. Core Modules
Explain core modules, responsibilities, and dependencies.

## 5. Request / Data Flow
Explain key request chains, task flows, or data processing processes.

## 6. Interfaces and Integrations
Explain integration with APIs, third-party services, messages, files, or external systems.

## 7. Configuration and Runtime
Explain configuration sources, running components, and deployment-related structures.

## 8. Testing Architecture
Explain test directories, test levels, and main verification entries.

## 9. Architectural Decisions
Record important design choices, compatibility strategies, and limitations.

## 10. Change Notes
Record important architectural changes and impacts.
```

---

## 6. Architecture Change Verification

After architecture-related modifications are completed, at least confirm:

- Whether original call chains still work.
- Whether new or modified interfaces have test coverage.
- Whether exception paths and compatible paths are verified.
- Modules, commands, and flows in documents are consistent with actual code.
- If deployment, dependencies, database, or security are involved, follow corresponding rule files simultaneously.
