# Experience Distillation and Problem Reuse Rules

## 1. Applicability

This file should be loaded when the task involves the following:

- Troubleshooting complex bugs or intermittent issues
- Encountering project-specific traps, compatibility limitations, or environment issues
- Searching for historical solutions
- Forming debugging steps reusable for subsequent tasks
- Needing to record important technical decision backgrounds

Project experience documents are maintained in:
`/docs/know-how.md`

---

## 2. Usage Principles

1. Before starting complex troubleshooting, check if `/docs/know-how.md` already has relevant records.
2. Prioritize reuse of already verified effective processing methods, avoiding repeated pitfalls.
3. New records must come from confirmed facts, successful verification, or clear observations.
4. One-time temporary output, unconfirmed guesses, or sensitive information should not be written into experience documents.
5. If old experience is outdated, update or mark the reason for invalidity rather than continuing to retain misleading information.

---

## 3. When `/docs/know-how.md` Must Be Updated

Experience documents should be updated when one of the following occurs:

- Fixed a problem prone to recurrence.
- Discovered important environment or compatibility limitations not stated in documentation.
- Found stable debugging, recovery, or verification steps.
- Identified project mechanisms prone to mis-modification, mis-configuration, or mis-operation.
- Formed testing or deployment experience worth reusing in subsequent tasks.
- Corrected existing incorrect experience.

No need to record:
- Obvious code changes.
- Information only related to current temporary input.
- Unverified guesses.
- Keys, tokens, production data, or sensitive logs.

---

## 4. Recommended Structure for `/docs/know-how.md`

```md
# Project Know-How

## 1. Quick Reference
List most important, most used precautions and verification commands.

## 2. Development Notes
Record special conventions, error-prone points, and recommended practices in project development.

## 3. Troubleshooting Records

### Issue: Brief issue name
- Symptom: Problem phenomenon
- Scope: Impact scope
- Root Cause: Confirmed cause
- Resolution: Verified resolution method
- Verification: Verification command or steps
- Prevention: How to avoid recurrence
- Date / Context: Optional background information

## 4. Environment Notes
Record platform differences, dependency limitations, installation, or runtime precautions.

## 5. Testing Notes
Record test environment requirements, failure-prone scenarios, and effective verification methods.

## 6. Deprecated Notes
Record invalidated experience and reasons for invalidity, delete obsolete content when necessary.
```

---

## 5. Troubleshooting Record Requirements

Good troubleshooting records should answer:

- What problem did the user or developer see?
- Under what circumstances does the problem occur?
- Is the root cause confirmed?
- What fix was adopted?
- How to verify the fix is effective?
- How to avoid repeated occurrence in the future?

Records should be concise, specific, and reproducible, and should not just be descriptions lacking context like "Fixed" or "Reinstall to solve."

---

## 6. Boundaries with Other Documents

- Project overall structure is written in `/docs/architecture.md`.
- User start, stop, config, and operation steps are written in `/docs/manual.md`.
- Environment and dependency requirements are written in `/requirements.md`.
- Project overview and quick entries are written in `/README.md` or `/readme.md`.
- Only reusable experience, problem root causes, and troubleshooting methods are written in `/docs/know-how.md`.
