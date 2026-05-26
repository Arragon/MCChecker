# Database, Migration, and Data Consistency Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Adding, deleting, or modifying tables, fields, indexes, constraints
- ORM model or database mapping changes
- SQL query, transaction, or persistence logic changes
- Data migration, data repair, or batch updates
- Cache and database consistency
- Data backup, recovery, rollback, or compatibility processing

---

## 2. Basic Principles

1. Data changes should aim for safety, verifiability, reversibility, or recoverability.
2. Do not delete, overwrite, or irreversibly modify existing data without explaining risks.
3. Data structure changes must consider existing data, old version code, and deployment order.
4. Query optimization must not be at the expense of correctness, permissions, or consistency.
5. When involving sensitive data, simultaneously load `/agent/docs/security.md`.

---

## 3. Pre-modification Check

Before implementing database-related modifications, confirm:

- Current database type and version requirements used.
- Model definition, migration mechanism, and initialization method.
- Involved data tables, fields, constraints, indexes, and associations.
- Existing queries, write flows, transactions, and tests.
- Whether there is already production data or compatibility requirements.
- Whether backup, migration, rollback, or phased release is needed.
- Whether API, configuration, deployment, or user operation is affected.

If production data status cannot be confirmed, do not assume data can be safely deleted or rewritten.

---

## 4. Schema Change Requirements

### 4.1 New Field or Table Addition
Consider:
- Default value and null strategy
- Old data compatibility
- Index and query performance
- Whether write and read flows are modified simultaneously
- Whether migration is repeatable or can fail safely

### 4.2 Field or Constraint Modification
Consider:
- Whether existing data satisfies new constraints
- Whether type conversion loses information
- Whether old code can still run
- Whether transition fields or phased migration are needed

### 4.3 Field or Table Deletion
High-risk operation, must:
1. Clearly explain deletion reason and impact.
2. Confirm that no code or task uses the data anymore.
3. Provide backup, migration, or recovery strategy.
4. Adopt phased approach of stopping writes first, then stopping reads, and finally deleting structure as much as possible.
5. Obtain explicit authorization before performing irreversible deletion.

---

## 5. Data Migration Requirements

Migration scripts or steps should ideally have:
- Clear preconditions
- Clear execution order
- Strategy for processing existing data
- Handling method when failed
- Rollback or recovery suggestions
- Verification method after execution

During migration, do not:
- Silently discard data that cannot be converted.
- Overwrite existing important content without prompts.
- Perform irreversible changes on all production data without confirming scope.

---

## 6. Queries and Transactions

When modifying read/write logic, focus on:
- Query result correctness
- Empty results and exception scenarios
- Concurrent updates and transaction boundaries
- Repeated submissions or idempotency
- N+1 queries or obvious performance degradation
- Whether permission filtering is still effective
- Whether cache needs to be invalidated or updated

---

## 7. Testing Requirements

Database-related changes should cover according to project capabilities:
- Read/write behavior of added or modified fields
- Normal data and boundary data
- Null values, duplicate values, invalid values, or constraint failure scenarios
- Compatible behavior before and after migration
- Transaction failure or rollback behavior, if applicable
- Permission and data isolation behavior, if applicable
- Basic evaluation of query performance impact, if modifying key queries

---

## 8. Document Synchronization

Update according to impact after database or data flow changes:
- `/docs/architecture.md`: Changes in data model, storage method, or data flow
- `/docs/manual.md`: Changes in initialization, migration, backup, recovery, or user maintenance steps
- `/requirements.md`: Changes in database type, version, or running preconditions
- `/README.md` or `/readme.md`: Changes in deployment entry or key running requirements
- `/docs/know-how.md`: When forming important migration, recovery, or troubleshooting experience

---

## 9. Delivery Description

In the final response, database-related tasks should clearly state:
- What structures, models, or queries were modified.
- Whether existing data migration is involved.
- Whether irreversible operations exist.
- How verification was executed.
- Whether pre-deployment backup, migration, or manual confirmation is needed.
- Whether rollback or recovery suggestions are provided.
