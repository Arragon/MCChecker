# SDA synthetic fixtures (M16-02)

These files are **synthetic contract-shape fixtures**, not real SDA data.

- `synthetic: true` on every fixture; no account, password, Cookie, CSRF token, HAR, real URL, or company configuration is stored here.
- `catalog/`, `table/`, `file/` each contain `success`, `auth_failed`, and `business_error` cases.
- `login/` contains `login_success`, `bad_credentials`, and `flow_changed`.
- `heartbeat/` contains `heartbeat_renewed`, `heartbeat_not_renewed`, and `heartbeat_expired`.

Real request bodies, login locators, SSO redirects, enterprise CA, maximum legal content size, and the actually-renewing lightweight request are **not** in this repository. They must be captured in an authorized intranet session (E2/E3), redacted, and recorded in `docs/M16_REAL_SDA_ACCEPTANCE.md` / Linear without secrets.

`heartbeat_validated=true` is **never** set by a fixture. Only a real idle-window session can set it; until then the UI/state must say “保活待验证”.

Run the offline contract test:

```bash
./.venv39/bin/python -m pytest -q tests/test_sda_protocol.py
```
