# Auth API Reference

Endpoint-level reference for the authentication surface: login, session refresh, logout,
password change, forgot-password, and the caller's own profile/portal view (`GET /me`).

**Scope note:** this doc covers only the endpoints a normal client (frontend app) calls.
It excludes:
- Super-admin endpoints (`/admin/societies`, `/admin/users`, `/admin/roles`, ...) — those live
  under a separate `/admin/*` prefix and are documented separately.
- `POST /auth/token` — an internal, undocumented (`include_in_schema=False`) OAuth2-form
  alias that exists only so Swagger UI's "Authorize" button works. It is not part of the
  public API and should not be called by client applications.

There is currently **no public signup/registration endpoint**. Accounts are created by a
super-admin or auto-provisioned by other modules (out of scope for this doc either way).

---

## How auth works

- **Bearer auth, not cookies.** Send the access token as `Authorization: Bearer <access_token>`.
  Tokens are returned as JSON body fields; nothing is set via `Set-Cookie`.
- **Access token** — a JWT (HS256), valid for **~15 minutes** by default. Claims include
  `user_id`, `active_society_id`, `role_ids`, `password_state`. Verification is stateless
  (no DB lookup per request).
- **Refresh token** — an opaque random string (not a JWT), valid for **~14 days** by default.
  Only its SHA-256 hash is stored server-side. It is **rotated on every use**: each call to
  `POST /auth/refresh` revokes the token you sent and returns a brand-new access/refresh pair.
  Do not reuse a refresh token you've already exchanged — a reused (already-rotated) token is
  treated as a **theft signal** and causes the server to revoke *all* of that user's refresh
  tokens, forcing every session to log in again.
- **Forced password change (`password_state: "must_change"`).** New accounts and accounts that
  just went through forgot-password have this state. While it's active, **every endpoint
  except `POST /auth/change-password`** (including `GET /me`) responds `403` until the password
  is changed.
- **No account enumeration.** `POST /auth/login` and `POST /auth/forgot-password` return the
  same generic response whether or not the email/account exists, so a caller cannot use them to
  discover which emails are registered.
- **No rate-limiting/lockout/CAPTCHA yet.** Brute-force protection on login is not implemented
  in the current build (planned before production) — don't assume repeated failed attempts get
  throttled.

## Common error envelope

Every error response (validation failures, auth failures, permission failures) uses the same
JSON shape:

```json
{
  "code": "authentication_error",
  "message": "Invalid email or password.",
  "details": {}
}
```

| HTTP status | `code`                 | Meaning |
|-------------|------------------------|---------|
| 422         | `validation_error`     | Request body failed validation (missing/malformed field, or a business rule like password policy). |
| 401         | `authentication_error` | Missing/invalid credentials or token. |
| 403         | `permission_denied`    | Authenticated, but not allowed to do this right now (e.g. forced password change). |

`details` is an object with extra machine-readable context (e.g. `{"field": "password"}`); it's
`{}` when there's nothing extra to report. Each endpoint section below lists its exact
`message` strings and `details` shapes — these are literal, not paraphrased.

---

## `POST /auth/login`

Authenticates with email + password and issues a new token pair. Public endpoint (no bearer
token required). Works even while `password_state` is `must_change` — the response tells the
client to redirect to change-password.

**Auth required:** No.

### Request

| Field      | Type   | Required | Notes |
|------------|--------|----------|-------|
| `email`    | string | Yes      | Not case-sensitive; not pre-validated as a strict email format client-side — invalid formats simply fail login with the generic error below. |
| `password` | string | Yes      | Plaintext, sent over HTTPS. |

```json
{
  "email": "priya.sharma@example.com",
  "password": "Sunshine24"
}
```

### Response — `200 OK`

| Field                | Type         | Notes |
|----------------------|--------------|-------|
| `access_token`       | string       | JWT, use as `Authorization: Bearer <access_token>`. |
| `refresh_token`      | string       | Opaque string, send to `POST /auth/refresh` later. |
| `token_type`         | string       | Always `"bearer"`. |
| `password_state`     | string       | `"active"` or `"must_change"`. If `"must_change"`, call `POST /auth/change-password` next — every other endpoint will 403. |
| `available_portals`  | string[]     | Distinct portals (`"admin"`, `"resident"`, `"platform"`) the account can use in its active society. If this has more than one entry, show a portal chooser before calling `GET /me`. |

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI0MiIsInVzZXJfaWQiOjQyLCJhY3RpdmVfc29jaWV0eV9pZCI6NywicGFzc3dvcmRfc3RhdGUiOiJhY3RpdmUiLCJpYXQiOjE3NTIwNDgwMDAsImV4cCI6MTc1MjA0ODkwMH0.dGhpc2lzYWZha2VzaWduYXR1cmU",
  "refresh_token": "rft_8f3Kz9QpXm2Vn7Lw1Yc0Ss6Rt4Bh5Gd_qWeRtYuIoPaSdFgHjKl",
  "token_type": "bearer",
  "password_state": "active",
  "available_portals": ["resident"]
}
```

### Errors

| Status | `code`                 | `message`                       | `details` | When |
|--------|------------------------|----------------------------------|-----------|------|
| 401    | `authentication_error` | `"Invalid email or password."`  | `{}`      | Unknown email, wrong password, inactive account, or an account with no roles in any society. All four cases return the **identical** message so the response can't be used to tell them apart. |
| 422    | `validation_error`     | `"Request validation failed."`  | `{"errors": [...]}` | `email` or `password` missing from the request body entirely (FastAPI schema validation, not a business rule). |

```json
{
  "code": "authentication_error",
  "message": "Invalid email or password.",
  "details": {}
}
```

---

## `POST /auth/refresh`

Exchanges a valid refresh token for a brand-new access/refresh pair. Public endpoint — the
refresh token itself is the credential; no bearer access token is needed.

**Auth required:** No (refresh token in body only).

### Request

| Field           | Type   | Required | Notes |
|-----------------|--------|----------|-------|
| `refresh_token` | string | Yes      | The refresh token from the last `login` or `refresh` response. |

```json
{
  "refresh_token": "rft_8f3Kz9QpXm2Vn7Lw1Yc0Ss6Rt4Bh5Gd_qWeRtYuIoPaSdFgHjKl"
}
```

### Response — `200 OK`

| Field           | Type   | Notes |
|-----------------|--------|-------|
| `access_token`  | string | New JWT — replaces the old one. |
| `refresh_token` | string | New refresh token — **replaces the one you sent**. The old one is now revoked; using it again will fail (see below). |
| `token_type`    | string | Always `"bearer"`. |

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI0MiIsInVzZXJfaWQiOjQyLCJhY3RpdmVfc29jaWV0eV9pZCI6NywicGFzc3dvcmRfc3RhdGUiOiJhY3RpdmUiLCJpYXQiOjE3NTIwNDg5MDAsImV4cCI6MTc1MjA0OTgwMH0.YW5vdGhlcmZha2VzaWduYXR1cmU",
  "refresh_token": "rft_2mNb6Vc3Xz9Lp0Ok8Ij7Uh5Yg4Tf1Rd_zAqWsExDrCfVgBhN",
  "token_type": "bearer"
}
```

### Errors

| Status | `code`                 | `message`                          | `details` | When |
|--------|------------------------|-------------------------------------|-----------|------|
| 401    | `authentication_error` | `"Invalid or expired session."`   | `{}`      | Token doesn't exist, is expired, or **has already been rotated/used once before** (this also revokes *every* refresh token this user currently holds, as a theft response — all of their other logged-in sessions will need to log in again), or the user is inactive/deleted. |
| 422    | `validation_error`     | `"Request validation failed."`    | `{"errors": [...]}` | `refresh_token` missing from the request body. |

```json
{
  "code": "authentication_error",
  "message": "Invalid or expired session.",
  "details": {}
}
```

---

## `POST /auth/logout`

Revokes a single refresh token (ends that one session). Idempotent — always succeeds, even if
the token is already invalid, expired, or unknown, so it's safe to call unconditionally on
client-side logout.

**Auth required:** No (refresh token in body only — there is no bearer-token check on this
route).

### Request

| Field           | Type   | Required | Notes |
|-----------------|--------|----------|-------|
| `refresh_token` | string | Yes      | The refresh token to revoke. |

```json
{
  "refresh_token": "rft_2mNb6Vc3Xz9Lp0Ok8Ij7Uh5Yg4Tf1Rd_zAqWsExDrCfVgBhN"
}
```

### Response — `200 OK`

| Field     | Type   | Notes |
|-----------|--------|-------|
| `message` | string | Confirmation message. |

```json
{
  "message": "Logged out."
}
```

### Errors

No domain error cases — this endpoint always returns `200`, even for an unknown or
already-revoked token.

| Status | `code`             | `message`                       | `details` | When |
|--------|--------------------|-----------------------------------|-----------|------|
| 422    | `validation_error` | `"Request validation failed."`  | `{"errors": [...]}` | `refresh_token` missing from the request body. |

---

## `POST /auth/change-password`

Changes the caller's own password. This is the **only** endpoint reachable while
`password_state` is `"must_change"` — use it to complete first-login or post-forgot-password
setup. On success, it also revokes all of the user's existing refresh tokens (all other
sessions are logged out).

**Auth required:** Yes — `Authorization: Bearer <access_token>`. Reachable during a forced
password change.

### Request

| Field              | Type   | Required | Notes |
|--------------------|--------|----------|-------|
| `current_password` | string | Yes      | The account's current (or temporary) password. |
| `new_password`     | string | Yes      | Must be **at least 8 characters** and contain **at least one letter and one digit**. Must differ from `current_password`. |

```json
{
  "current_password": "Sunshine24",
  "new_password": "Monsoon2026!"
}
```

### Response — `200 OK`

| Field     | Type   | Notes |
|-----------|--------|-------|
| `message` | string | Confirmation message. |

```json
{
  "message": "Password changed. Please log in again."
}
```

### Errors

| Status | `code`                 | `message`                                                    | `details` | When |
|--------|------------------------|----------------------------------------------------------------|-----------|------|
| 401    | `authentication_error` | `"Not authenticated."`                                        | `{}`      | No bearer token sent, or it's blank. |
| 401    | `authentication_error` | `"Invalid or expired token."`                                  | `{}`      | Access token failed to decode (expired, tampered, wrong algorithm). |
| 401    | `authentication_error` | `"Invalid token."`                                             | `{}`      | Token's `user_id` claim is missing/malformed, or that user no longer exists / is inactive. |
| 401    | `authentication_error` | `"Current password is incorrect."`                             | `{}`      | `current_password` doesn't match. |
| 422    | `validation_error`     | `"Password must be at least 8 characters."`                    | `{"field": "password"}` | `new_password` shorter than 8 characters. |
| 422    | `validation_error`     | `"Password must contain at least one letter and one digit."`   | `{"field": "password"}` | `new_password` is all letters or all digits. |
| 422    | `validation_error`     | `"New password must be different from the current password."` | `{"field": "new_password"}` | `new_password` equals `current_password`. |

```json
{
  "code": "validation_error",
  "message": "Password must be at least 8 characters.",
  "details": {"field": "password"}
}
```

---

## `POST /auth/forgot-password`

Requests a password reset. Always returns the same generic `200` response, whether or not the
email belongs to a real, active account — this is intentional (no enumeration). If the account
exists, a temporary password is emailed to it (via the configured `EmailSender`; in dev this is
logged to the terminal instead of actually sent), the account's `password_state` is set to
`must_change`, and all of its existing sessions are revoked. There is no separate
"reset-password" endpoint — the flow is: receive the temp password by email → `POST
/auth/login` with it → `POST /auth/change-password` to set a real password.

**Auth required:** No.

### Request

| Field   | Type   | Required | Notes |
|---------|--------|----------|-------|
| `email` | string | Yes      | The account's email. |

```json
{
  "email": "priya.sharma@example.com"
}
```

### Response — `200 OK`

Always this response, regardless of whether the email is registered:

| Field     | Type   | Notes |
|-----------|--------|-------|
| `message` | string | Generic confirmation — does not confirm or deny the email exists. |

```json
{
  "message": "If an account exists for that email, a temporary password has been sent."
}
```

### Errors

No domain error cases are raised — invalid, unknown, or inactive emails are all silently
absorbed and still return the `200` above.

| Status | `code`             | `message`                       | `details` | When |
|--------|--------------------|-----------------------------------|-----------|------|
| 422    | `validation_error` | `"Request validation failed."`  | `{"errors": [...]}` | `email` missing from the request body entirely. |

---

## `GET /me`

Returns the caller's own profile plus a view of what their current session should show:
available portals, the resolved active portal, that portal's visible modules, its landing
page, and permission hints. Call this right after login (and after a portal switch) to build
the app shell.

**Auth required:** Yes — `Authorization: Bearer <access_token>`. **Not** reachable during a
forced password change (see errors below) — resolve `password_state: "must_change"` via
`POST /auth/change-password` first.

### Request

| Param     | Type   | Required | Notes |
|-----------|--------|----------|-------|
| `portal`  | string (query) | No | Which portal to activate, e.g. `?portal=admin`. Max 16 characters. Only takes effect if it's one of the caller's `available_portals` — an invalid or unavailable value is **not an error**; it's silently ignored (see response notes below). Omit it when the account has only one portal. |

No request body.

### Response — `200 OK`

| Field                | Type            | Notes |
|----------------------|-----------------|-------|
| `user.id`            | integer         | Caller's user id. |
| `user.email`         | string          | Caller's email. |
| `user.full_name`     | string \| null  | Display name. |
| `user.phone`         | string \| null  | Phone number. |
| `active_society_id`  | integer \| null | The society this session is scoped to. `null` for a super-admin or an account with no active society. |
| `available_portals`  | string[]        | All portals this account can use in the active society. |
| `active_portal`      | string \| null  | The resolved portal for this request: the requested `?portal=` if valid, else the sole portal if there's only one, else `null` (e.g. multiple portals available but none validly requested). View-only — never affects what the caller is authorized to do. |
| `modules`            | string[]        | Module keys visible for `active_portal` (empty if `active_portal` is `null`). |
| `landing`            | string \| null  | Suggested landing page key for `active_portal` (e.g. `"notices"`, `"dashboard"`, `"admin"`); `null` if `active_portal` is `null`. |
| `permissions`        | string[]        | Union of this account's permission keys in the active society. Hints only — the server re-checks permissions on every request regardless of this list. |
| `onboarding_required`| boolean         | `true` if the active society hasn't finished onboarding yet — the frontend should route to the onboarding wizard. |

**Example — single-portal resident**, `GET /me` (no query param needed):

```json
{
  "user": {
    "id": 42,
    "email": "priya.sharma@example.com",
    "full_name": "Priya Sharma",
    "phone": "+91-9876543210"
  },
  "active_society_id": 7,
  "available_portals": ["resident"],
  "active_portal": "resident",
  "modules": ["complaints", "notices", "vault"],
  "landing": "notices",
  "permissions": ["complaints.create", "notices.read", "vault.read"],
  "onboarding_required": false
}
```

**Example — dual-portal account** (society admin who is also a resident), `GET /me?portal=admin`:

```json
{
  "user": {
    "id": 15,
    "email": "vikram.shah@example.com",
    "full_name": "Vikram Shah",
    "phone": "+91-9123456789"
  },
  "active_society_id": 7,
  "available_portals": ["admin", "resident"],
  "active_portal": "admin",
  "modules": ["complaints", "dashboard", "finance", "houses", "notices", "vault"],
  "landing": "dashboard",
  "permissions": [
    "complaints.assign",
    "complaints.read_all",
    "finance.manage",
    "houses.manage",
    "notices.publish",
    "vault.manage"
  ],
  "onboarding_required": false
}
```

Calling this same account with no `?portal=` (or an invalid value) resolves `active_portal` to
`null` — since there's more than one available portal and none was validly requested — giving
`"modules": []` and `"landing": null`. Always pass `?portal=` explicitly for multi-portal
accounts (use the chooser shown after login when `available_portals` has more than one entry).

**Example — super-admin** (fixed platform shell, not scoped to any society):

```json
{
  "user": {
    "id": 1,
    "email": "superadmin@example.com",
    "full_name": "Platform Owner",
    "phone": null
  },
  "active_society_id": null,
  "available_portals": ["platform"],
  "active_portal": "platform",
  "modules": [],
  "landing": "admin",
  "permissions": [],
  "onboarding_required": false
}
```

### Errors

| Status | `code`                 | `message`                                             | `details` | When |
|--------|------------------------|----------------------------------------------------------|-----------|------|
| 401    | `authentication_error` | `"Not authenticated."`                                   | `{}`      | No bearer token sent, or it's blank. |
| 401    | `authentication_error` | `"Invalid or expired token."`                             | `{}`      | Access token failed to decode (expired, tampered, wrong algorithm). |
| 401    | `authentication_error` | `"Invalid token."`                                        | `{}`      | Token's `user_id` claim is missing/malformed, or that user no longer exists / is inactive. |
| 403    | `permission_denied`    | `"Password change required before continuing."`          | `{"password_state": "must_change"}` | Caller's `password_state` is `must_change` — call `POST /auth/change-password` first. |

```json
{
  "code": "permission_denied",
  "message": "Password change required before continuing.",
  "details": {"password_state": "must_change"}
}
```

An out-of-range `?portal=` value (over 16 characters) triggers a generic `422
validation_error` (`"Request validation failed."`) from request parsing — this is unrelated to
whether the portal name is one the account actually has.
