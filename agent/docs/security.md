# Security and Sensitive Information Handling Rules

## 1. Applicability

This file must be loaded when the task involves any of the following:

- Login, registration, identity authentication, or permission control
- Token, Cookie, Session, password, key, or certificate
- User input, rich text, URL, file upload, or file download
- External HTTP request, Webhook, third-party API, or callback
- Shell command, child process, script execution, or path operation
- Logs, error output, audit records, or debug information
- Data export, personal information, or sensitive business data
- Deployment configuration, environment variables, or production credentials

---

## 2. Absolutely Prohibited Items

Agent must not:

- Write real passwords, tokens, API Keys, Secrets, Cookies, private keys, or production connection strings into code, documentation, tests, or logs.
- Expose discovered sensitive information in the final response.
- Put server-side keys into browser-visible code or static resources.
- Rely solely on frontend hidden elements for permission control.
- Execute user-provided commands, paths, SQL, templates, or HTML without validation.
- Turn off necessary security validation due to fixing problems, unless the task explicitly requires it and serious risks have been explained.

If suspected real credentials are found in the repository, avoid copying their content and prompt the user to perform rotation and security processing.

---

## 3. Input and Output Security

When processing external input, consider according to scenario:

- Type, length, format, and allowed value validation
- Protection against unauthorized access
- HTML / script injection protection
- SQL, command, path, or template injection protection
- File type, size, path, and storage location restrictions
- URL redirection or server-side request forgery risks
- Information leakage risks in error responses

Information output to users should:

- Be sufficient to help handle the problem.
- Not contain unnecessary sensitive information such as internal stacks, database details, keys, user privacy, or system paths.

---

## 4. Authentication and Permission

When involving identity and permission, confirm:

- Permission validation occurs at trusted server-side boundaries.
- No unauthorized access exists between different roles or resource owners.
- Default permissions follow the principle of least privilege.
- Unauthenticated and unauthorized behaviors have clear, stable responses.
- New interfaces, page operations, or background tasks will not bypass existing permission control.
- Supplement tests for key permission paths.

---

## 5. Logging and Error Handling

Logging should:

- Record sufficient context to support problem location.
- Omit or desensitize sensitive fields.
- Avoid outputting full request headers, authentication info, session content, or user privacy.
- Avoid printing secrets from third-party responses as-is.

Error handling should:

- Provide safe and understandable error information to users.
- Retain non-sensitive logs for developers to locate problems.
- Supplement tests or verification for abnormal failure paths.

---

## 6. Configuration and Secret Management

- Keys and passwords should be provided through the project's existing secure configuration mechanisms, e.g., environment variables or secret management services.
- Example files only provide variable names and safe placeholders.
- `.env` examples must not contain real credentials.
- When configuration changes affect user deployment, update `/docs/manual.md` and `/requirements.md`, but still do not record real secrets.

---

## 7. External Calls and File Operations

### External Requests
Consider:
- Timeout
- Retry and failure handling
- URL or target scope restrictions
- Authentication info protection
- Response content validation
- Log desensitization

### File Operations
Consider:
- Path traversal
- File overwriting
- File type and size restrictions
- Unauthorized file access
- Uploaded content execution risks
- Temporary file cleanup

### Command Execution
If system commands must indeed be called:
- Prioritize use of safe APIs or parameterized methods.
- Avoid string concatenation of unvalidated user input.
- Limit executable commands, parameters, and running permissions.
- Clarify failure, timeout, and cleanup strategies.

---

## 8. Security Verification

When involving security behavior changes, verify at least relevant content:

- Legal input works normally.
- Illegal input is rejected or safely handled.
- Unauthenticated and unauthorized access is blocked.
- Logs and error outputs do not leak sensitive data.
- Configuration and deployment documents do not contain real secrets.
- Fixed content has corresponding tests or clear manual verification steps.
