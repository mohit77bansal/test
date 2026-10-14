# Vendor Portal — Architecture (Stage 1)

Source of truth: `docs/vendor-portal-spec.pdf` (v1.0, Oct 14 2026). This document is the
Stage 1 deliverable. Where it resolves a gap in the spec, the resolution is marked
**[Resolution]** and should be written back into the spec when approved.

> Status: **awaiting approval** by Mohit / lead engineer. Build work on this branch was
> started in parallel at the owner's request while they were away; anything here that
> changes on review will be changed in the code too.

---

## 1. Spec review — gaps, contradictions, risky assumptions

| # | Issue | Resolution |
|---|-------|------------|
| 1 | What happens to in-flight invoices when the approval matrix changes? | **[Resolution]** Routing is **snapshotted at submit**: `invoices.required_level` (L1/L2/L3) is computed from the matrix when the vendor submits and never recomputed. Matrix edits only affect invoices submitted afterwards (matches `test_admin_config`). |
| 2 | Which amount drives routing — taxable or total? | **[Resolution]** `total` (incl. GST). Bands are inclusive: `total <= l1_max` stops at L1; `total <= l2_max` stops at L2; otherwise L3. |
| 3 | `SUBMITTED` and `FIN_PRECHECK` are both listed; nothing moves an invoice between them. | **[Resolution]** Submit writes a `SUBMITTED` transition (email + audit) and the same transaction moves the invoice to `FIN_PRECHECK`. `SUBMITTED` is therefore a recorded, transient state. |
| 4 | "Query → back to the same stage" — the stage must be remembered. | **[Resolution]** `invoices.query_level` stores the level that raised the query; vendor reply restores `PENDING_<level>` / `FIN_PRECHECK`. |
| 5 | "Caller within their amount band" is ambiguous, since every invoice starts at L1. | **[Resolution]** A user may act at level X only if (a) the invoice's `current_level == X`, (b) they hold role X (L1 additionally: they own the cost centre or hold an L1 role scoped to it / unscoped), (c) X is within the invoice's snapshotted `required_level`, (d) they are not the uploader and have not acted on this invoice at an earlier level (incl. FIN_PRE). |
| 6 | "Finance cannot approve at L1–L3" vs. "internal users can hold more than one role". | **[Resolution]** Enforced per invoice via rule 5(d): whoever did the Finance pre-check on an invoice can never approve it at L1–L3. A user with FINANCE + L2 may approve *other* invoices at L2. |
| 7 | Expected payment date in the "approved" email is undefined. | **[Resolution]** `due_date = approval date + PAYMENT_TERMS_DAYS` (default 30, `SystemSetting`). MSME 45-day logic is Phase 2. |
| 8 | When is an invoice PAID vs PARTLY_PAID with TDS? | **[Resolution]** Outstanding = `total − Σ payments.amount − Σ payments.tds_deducted`. Outstanding ≤ ₹0.00 → `PAID`, else `PARTLY_PAID`. Overpayment is rejected. |
| 9 | Initial bank account at onboarding — who approves it? | **[Resolution]** The first bank account is verified by the Vendor Manager as part of KYC (cancelled cheque) and becomes `ACTIVE` on vendor activation. Every *later* change creates a `PENDING` version that needs **Finance** approval; payments for that vendor are blocked while a pending version exists. |
| 10 | GSTIN change after activation. | **[Resolution]** A new/changed GSTIN is added as `verified_at = NULL`; invoices referencing an unverified GSTIN fail the GSTIN-match validation until the Vendor Manager verifies it. |
| 11 | Postgres RLS is bypassed by superusers and table owners. | **[Resolution]** App connects as non-superuser `vp_app`; every vendor-owned table has `ENABLE` **and** `FORCE ROW LEVEL SECURITY`. |
| 12 | "Middleware runs `SET LOCAL` inside ATOMIC_REQUESTS" — Django's ATOMIC_REQUESTS wraps only the view, not middleware. | **[Resolution]** `core.middleware.VendorIsolationMiddleware` opens its own `transaction.atomic()` around the whole request and runs `set_config('app.vendor_id', …, true)` (= `SET LOCAL`). Session default (set on connect) is `app.is_internal='on'` for Celery / management commands; the middleware sets it `off` for every vendor *and anonymous* request. |
| 13 | `audit_log` "no UPDATE/DELETE grants" doesn't work for the owning role. | **[Resolution]** A `BEFORE UPDATE OR DELETE` trigger raises an exception. Grants are also revoked in prod (`docker/postgres/init.sql`). |
| 14 | PAN must be encrypted, but rule checks need PAN-in-GSTIN. | **[Resolution]** `pan_enc` (encrypted) + `pan_last4`; services decrypt on demand. Encryption via `core.crypto` with a pluggable key provider: `FIELD_ENCRYPTION_KEY` (Fernet) locally, KMS data key in UAT/prod. |
| 15 | OTP "for vendors" (security section) vs. OTP for all logins (feature 10). | **[Resolution]** OTP for all users; `OTP_ENABLED` setting (default on). Internal users move to Entra SSO in Phase 2. |
| 16 | LLM normalisation — provider unspecified, must stay in ap-south-1. | **[Resolution]** `parsing.normalise` interface with `none` (rule-based mapping, default) and `bedrock` engines (`LLM_ENGINE`). Bedrock keeps documents in AWS ap-south-1. |
| 17 | Reject is final — is the invoice number burnt? | **[Resolution]** The duplicate check ignores `REJECTED` invoices, so the vendor can re-upload the same invoice number after a reject. |
| 18 | Vendor statuses after REJECTED. | **[Resolution]** `REJECTED` is terminal for that vendor record; Vendor Manager can re-invite (new record). `BLOCKED ↔ ACTIVE` (unblock) is allowed. |
| 19 | "Each test resets the database to the seed state." | Unit + e2e tests use pytest-django transactional isolation; seed data is built by fixtures (`tests/conftest.py`) from the same `core.seed` functions the `seed` management command uses. |
| 20 | Tailwind standalone CLI needs a binary. | Built in the Docker image (`tailwindcss` standalone downloaded at build time). A pre-built `static/css/app.css` is committed so local dev needs no Node. |

## 2. Django app layout

Confirmed as in the spec, with one addition (`portal` for the two page shells):

| App | Owns |
|-----|------|
| `core` | `BaseModel` (UUID pk, created/updated at/by), vendor-isolation middleware, RLS helpers + migration ops, `permissions.py`, `crypto.py`, `SystemSetting`, `CostCentre`, seed functions, `/healthz` |
| `accounts` | custom `User`, `Role`, `UserRole`, login + OTP, password reset, idle timeout, role landing, console user/role admin |
| `vendors` | `Vendor`, `VendorUser`, `VendorGSTIN`, `VendorBankAccount`, `DocumentType`, `VendorDocument`, `VendorInvite`; invite, registration, KYC upload/review, state machine, bank change |
| `invoices` | `Invoice`, `InvoiceLineItem`, `ApprovalMatrix`, `ApprovalAction`, `InvoiceMessage`, `Payment`, `PurchaseOrder` (Phase 2 shell); `services.py` state machine |
| `parsing` | `File`, `ParseJob`, `parse_document(kind, file_id)`, engines `fake` / `textract`, rule checks, virus scan interface |
| `notify` | `EmailTemplate`, `Notification`, `EmailSender` (`solfin_api`, `console`), Celery send task, bell |
| `reports` | report views, CSV/XLSX export helper |
| `audit` | `AuditLog` (append-only), request-context middleware, login signals |

## 3. Model design

All tables live in schema `vp` (search_path `vp,public`). Every table: `id uuid pk`,
`created_at`, `updated_at`, `created_by_id`, `updated_by_id` (nullable FK users).
Money: `numeric(14,2)`.

| Model / table | Fields (beyond base) | Constraints / indexes | RLS |
|---|---|---|---|
| `users` | email (unique, citext-like lower index), name, phone, password (argon2id), user_type (VENDOR/INTERNAL), status (ACTIVE/INACTIVE), mfa_enabled, last_login_at | unique lower(email) | no |
| `roles` | code (unique), name | | no |
| `user_roles` | user→users, role→roles, cost_centre→cost_centres null | unique(user, role, cost_centre) | no |
| `cost_centres` | code (unique), name, l1_owner→users null, is_active | | no |
| `vendors` | vendor_code (unique, `V#####`), legal_name, trade_name, category (GOODS/SERVICES/PROFESSIONAL), pan_enc, pan_last4, constitution, msme_status, udyam_no, msme_type, contact_name, contact_email, contact_phone, address, status, owner_manager→users, invited_by→users, activated_at, block_reason, consent_at | index(status) | `id` |
| `vendor_invites` | vendor→vendors, email, token_hash (unique), expires_at, used_at | | no (token lookup is pre-auth) |
| `vendor_users` | vendor, user (unique), is_admin, is_active | | vendor_id |
| `vendor_gstins` | vendor, gstin, state_code, address, is_primary, verified_at | unique(vendor, gstin) | vendor_id |
| `vendor_bank_accounts` | vendor, holder_name, account_no_enc, account_last4, ifsc, bank_name, status (PENDING/ACTIVE/REJECTED/SUPERSEDED), verified_at, approved_by→users, version | index(vendor,status) | vendor_id |
| `document_types` | code (unique), name, applies_to (json list of categories), mandatory, has_expiry, parser_profile (PAN/GST/CHEQUE/UDYAM/NONE), is_active, sort_order | | no |
| `files` | vendor null, s3_key, bucket, original_name, mime, size_bytes, sha256, uploaded_by, scan_status (PENDING/CLEAN/INFECTED/SKIPPED) | index(vendor, sha256) | vendor_id (NULL visible to internal only) |
| `vendor_documents` | vendor, document_type, file, valid_from, valid_to, status (UPLOADED/APPROVED/REJECTED), reviewer, remarks, parse_job null | index(vendor, document_type) | vendor_id |
| `parse_jobs` | vendor null, file, kind (INVOICE/PAN/GST/CHEQUE/UDYAM), engine, status (QUEUED/RUNNING/DONE/FAILED), raw_json, extracted_json, confidence (json field→score), error, duration_ms | | vendor_id |
| `approval_matrix` | category null, cost_centre null, l1_max, l2_max, requires_l3_above, is_active | most-specific match wins: (category+cc) > cc > category > default | no |
| `purchase_orders` | Phase 2 shell (po_no, vendor, cost_centre, amount, consumed_amount, valid_till, status) | | vendor_id |
| `invoices` | vendor, gstin→vendor_gstins, invoice_no, invoice_date, fy (`2026-27`), po null, cost_centre, taxable, cgst, sgst, igst, total, tds_section, tds_amount, file, parse_job, status, current_level, required_level, query_level, due_date, portal_ref (unique, `INV-YYYY-#####`), submitted_by, submitted_at, approved_at | partial unique(vendor, invoice_no, fy) where status ≠ REJECTED; index(status, current_level) | vendor_id |
| `invoice_line_items` | vendor, invoice, description, hsn_sac, qty, rate, amount, tax_rate | | vendor_id |
| `approval_actions` | vendor, invoice, level (FIN_PRE/L1/L2/L3/VENDOR), actor, action (APPROVE/REJECT/QUERY/REPLY/SUBMIT), comment, acted_at | index(invoice) | vendor_id |
| `invoice_messages` | vendor, invoice, from_user, body, file null | | vendor_id |
| `payments` | vendor, invoice, amount, paid_on, utr, tds_deducted, recorded_by | | vendor_id |
| `email_templates` | code (unique), subject, enabled | | no |
| `notifications` | user null, to_email, template_code, payload json, channel (EMAIL/IN_APP), status (QUEUED/SENT/FAILED/SKIPPED), sent_at, error, read_at, link | index(user, read_at) | no (filtered by user) |
| `audit_log` | actor null, entity, entity_id, action, before_json, after_json, ip, user_agent, at | index(entity, entity_id), index(at); UPDATE/DELETE trigger raises | no |
| `system_settings` | key (unique), value json | | no |

**RLS policy SQL** (applied by `core.rls.EnableRLS` migration op for each vendor-owned table;
`vendors` uses `id` instead of `vendor_id`):

```sql
ALTER TABLE vp.<t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE vp.<t> FORCE ROW LEVEL SECURITY;
CREATE POLICY vendor_isolation ON vp.<t>
  USING (
    current_setting('app.is_internal', true) = 'on'
    OR vendor_id::text = current_setting('app.vendor_id', true)
  )
  WITH CHECK (
    current_setting('app.is_internal', true) = 'on'
    OR vendor_id::text = current_setting('app.vendor_id', true)
  );
```

**How `app.vendor_id` is set:** on each new DB connection (`connection_created` signal)
`SET app.is_internal = 'on'; SET app.vendor_id = ''`. `VendorIsolationMiddleware` (after auth)
wraps the request in `transaction.atomic()` and runs
`select set_config('app.is_internal', <on|off>, true), set_config('app.vendor_id', <id|''>, true)`.
Vendor users → `off` + their vendor id; anonymous → `off` + `''`; internal → `on`.
`SET LOCAL` semantics mean values revert at commit, so pooled connections never leak.
The ORM layer adds `VendorScopedManager.for_user(user)` which filters by vendor for vendor
users — RLS is the backstop if a view forgets it.

## 4. URL and view map

Every interactive element carries `data-testid`. Convention: `<entity>-<action>-btn`,
`<entity>-row-<ref>`, form inputs use Django field names (`name=`), status badges
`data-testid="status-badge"`.

### Public / auth (`accounts`)
| URL | View | Template | Permission | Key testids |
|---|---|---|---|---|
| `/` | `landing` | — | any → redirect | |
| `/login/` | `LoginView` | `accounts/login.html` | anon | `login-email`, `login-password`, `login-submit-btn`, `login-error` |
| `/login/otp/` | `OtpView` | `accounts/otp.html` | pending-OTP session | `otp-code`, `otp-submit-btn`, `otp-error` |
| `/logout/` | `logout_view` | — | auth | `logout-btn` |
| `/password-reset/…` | Django auth views | `accounts/password_reset*.html` | anon | `reset-email`, `reset-submit-btn`, `new-password1/2`, `reset-confirm-btn` |
| `/healthz` | `healthz` | JSON | any | |

### Vendor portal `/vendor/`
| URL | View | Perm | HTMX fragments | testids |
|---|---|---|---|---|
| `/vendor/invite/<token>/` | accept invite (set password) | anon + valid token | | `invite-password1/2`, `invite-accept-btn` |
| `/vendor/` | dashboard (redirects to onboarding until ACTIVE) | vendor | | `vendor-dashboard` |
| `/vendor/onboarding/` | registration form | `vendor.edit_profile` | | `registration-save-btn` |
| `/vendor/onboarding/documents/` | KYC checklist + upload | `vendor.edit_profile` | `partials/parse_status.html` | `doc-upload-<code>`, `doc-upload-btn-<code>`, `doc-status-<code>`, `parse-mismatch` |
| `/vendor/onboarding/submit/` | submit for review (POST) | `vendor.edit_profile` | | `onboarding-submit-btn` |
| `/vendor/invoices/` | list | `invoice.view_own` | | `invoice-row-<ref>`, `invoice-upload-link`, `export-csv-btn` |
| `/vendor/invoices/upload/` | upload file | `invoice.upload` | | `invoice-file`, `invoice-upload-btn` |
| `/vendor/invoices/<id>/edit/` | review parsed fields, submit | `invoice.upload` | `partials/parse_status.html` (polls every 2s) | `parse-reading`, `field-<name>`, `low-confidence`, `invoice-submit-btn`, `form-error` |
| `/vendor/invoices/<id>/` | detail, status, messages, payments | `invoice.view_own` | `partials/messages.html` | `status-badge`, `reply-body`, `reply-send-btn`, `payment-row` |
| `/vendor/payments/` | payment history | `invoice.view_own` | | `payment-row` |
| `/vendor/users/` | manage vendor users | `vendor.manage_users` | | `vendor-user-add-btn`, `vendor-user-remove-btn` |
| `/vendor/bank/` | bank change request | `vendor.edit_bank` | | `bank-change-submit-btn` |
| `/vendor/files/<id>/` | file download (perm-checked → presigned redirect) | vendor owns | | |
| `/notifications/` | bell list (shared) | auth | `partials/bell.html` | `bell`, `bell-count` |

### Internal console `/console/`
| URL | View | Perm | testids |
|---|---|---|---|
| `/console/` | role landing / my queue | internal | `queue-table`, `queue-row-<ref>` |
| `/console/vendors/` | vendor list | `vendor.view_all` | `vendor-row-<code>`, `vendor-invite-link` |
| `/console/vendors/invite/` | invite | `vendor.invite` | `invite-submit-btn` |
| `/console/vendors/<id>/` | vendor detail + KYC review | `vendor.view_all` | `doc-approve-btn-<code>`, `doc-reject-btn-<code>`, `doc-remarks-<code>`, `vendor-sendback-btn`, `vendor-activate-btn`, `vendor-reject-btn`, `vendor-block-btn`, `vendor-unblock-btn`, `gstin-verify-btn` |
| `/console/bank-changes/` | pending bank changes | `bank.approve` | `bank-approve-btn`, `bank-reject-btn` |
| `/console/invoices/` | all invoices (filters) | `invoice.view_all` | `invoice-row-<ref>` |
| `/console/invoices/<id>/` | detail + actions | `invoice.view_all` | `invoice-approve-btn`, `invoice-reject-btn`, `invoice-query-btn`, `action-comment`, `action-error`, `tds_section`, `tds_amount` |
| `/console/payments/queue/` | approved, awaiting payment | `payment.record` | `payment-record-link` |
| `/console/invoices/<id>/pay/` | record payment | `payment.record` | `payment-submit-btn` |
| `/console/reports/<name>/` | vendor-master, ageing, pending-by-approver, payments | `report.view` | `report-table`, `export-csv-btn`, `export-xlsx-btn` |
| `/console/files/<id>/` | file download | internal + `file.view` | |
| `/console/admin/users/` | users & roles | `admin.users` | `user-add-btn`, `role-assign-btn` |
| `/console/admin/matrix/` | approval matrix | `admin.matrix` | `matrix-save-btn` |
| `/console/admin/documents/` | document checklist | `admin.checklist` | `doctype-save-btn` |
| `/console/admin/templates/` | email templates on/off | `admin.templates` | `template-toggle-<code>` |
| `/console/audit/` | audit log | `audit.view` | `audit-row` |
| `/sysadmin/` | Django admin | superuser | |

## 5. State machines

### Vendor
| From | To | Who | Side effects |
|---|---|---|---|
| — | INVITED | VENDOR_MANAGER (`vendor.invite`) | invite token (7 days, `INVITE_EXPIRY_DAYS`), email `vendor_invited` (cc VM), audit |
| INVITED | DRAFT | invitee accepts link + sets password | user + VendorUser(is_admin), audit |
| DRAFT | SUBMITTED | VENDOR_ADMIN; all mandatory docs uploaded, profile complete, consent | email `registration_submitted` → VM, audit |
| SUBMITTED | UNDER_REVIEW | VM opens review (first review action) | audit |
| SUBMITTED/UNDER_REVIEW | DRAFT | VM ≠ inviter (send back, remarks) | email `kyc_sent_back` → Vendor Admin |
| UNDER_REVIEW/SUBMITTED | ACTIVE | VM ≠ inviter; all mandatory docs APPROVED | first bank account → ACTIVE, email `vendor_activated` → all vendor users (cc VM), audit |
| SUBMITTED/UNDER_REVIEW | REJECTED | VM ≠ inviter | audit (terminal) |
| ACTIVE | BLOCKED | VM | email `vendor_blocked` (reason), audit |
| BLOCKED | ACTIVE | VM | audit |

Bank account: `PENDING → ACTIVE` (Finance, `bank.approve`, ≠ requester; previous ACTIVE → SUPERSEDED) or `PENDING → REJECTED`. Request emails `bank_change_requested` → VM + Finance, cc Vendor Admin.

### Invoice
| From | To | Who | Side effects |
|---|---|---|---|
| DRAFT | SUBMITTED → FIN_PRECHECK | vendor user (`invoice.upload`), vendor ACTIVE, validations pass | snapshot `required_level`, `portal_ref`, email `invoice_submitted` (uploader + VA, cc AP inbox), `approval_pending` → Finance |
| FIN_PRECHECK | PENDING_L1 | FINANCE (sets tds_section/amount) | `approval_pending` → L1 pool |
| PENDING_L1 | APPROVED | L1, if required_level = L1 | due_date, `invoice_approved` |
| PENDING_L1 | PENDING_L2 | L1, if required_level ≥ L2 | `approval_pending` → L2 pool |
| PENDING_L2 | APPROVED / PENDING_L3 | L2 | as above |
| PENDING_L3 | APPROVED | L3 | `invoice_approved` |
| FIN_PRECHECK/PENDING_Lx | QUERY_RAISED | current-level actor, comment required | `query_level` set, `query_raised` (uploader + VA, cc approver) |
| QUERY_RAISED | (query_level state) | vendor reply (message ± file) | `vendor_replied` → approver who raised |
| FIN_PRECHECK/PENDING_Lx | REJECTED | current-level actor, comment required | `invoice_rejected` (terminal) |
| APPROVED/PARTLY_PAID | PARTLY_PAID/PAID | FINANCE (`payment.record`), no pending bank change | Payment row, `payment_recorded` (VA, cc uploader) |

Every transition: `ApprovalAction` + `AuditLog` in the same transaction; emails enqueued
`on_commit`. Views never set `status` directly — only `invoices.services`.

## 6. Permission matrix

Generated from `apps/core/permissions.py` (`python manage.py permission_matrix`).
See `docs/PERMISSIONS.md` (regenerated by the command; CI checks it is current).

## 7. Interfaces and seams

| Seam | Setting | Engines | Tests use |
|---|---|---|---|
| `notify.senders.get_sender()` → `EmailSender.send(to, cc, subject, html, text)` | `EMAIL_SENDER` | `solfin_api` (HTTP, endpoint/key from env), `console` (Django mail backend) | `console` + `locmem` backend → `mail.outbox` |
| `parsing.parse_document(kind, file_id)` | `PARSER_ENGINE` | `textract` (AnalyzeExpense / AnalyzeDocument + rule checks + `LLM_ENGINE` normaliser), `fake` | `fake`: deterministic fixtures; a `FAKE_PARSE:{json}` marker inside the uploaded file overrides fields |
| `parsing.scan.scan_file(file)` | `VIRUS_SCAN_ENGINE` | `clamd`, `none` | `none` |
| storage | `STORAGE_BACKEND` | `s3` (django-storages, presigned 5-min URLs), `local` (FileSystemStorage, streamed download) | `local` (unit/e2e), MinIO in `docker-compose.test.yml` |
| `core.crypto` | `FIELD_ENCRYPTION_KEY` / `KMS_KEY_ID` | Fernet, KMS envelope | Fernet |
| Celery | `CELERY_TASK_ALWAYS_EAGER` | Redis broker | eager |

## 8. Test plan

Seed (`apps/core/seed.py`, used by `manage.py seed` and `tests/conftest.py`):
roles, document types (PAN, GST_CERT, CANCELLED_CHEQUE, MSME, AGREEMENT), cost centre
`CC-OPS` (owner `l1@test`), matrix default (L1 ≤ ₹1,00,000, L2 ≤ ₹10,00,000, L3 above),
email templates. Demo/test fixtures: one user per role (`vm@`, `l1@`, `l2@`, `l3@`,
`finance@`, `admin@`, `auditor@` `…test.local`, password `Passw0rd!234`); vendor A
(`vendora@test.local`, ACTIVE, GSTIN `27AAACA1234A1Z5`) and vendor B (`vendorb@…`, ACTIVE);
invoices at ₹50,000 and ₹15,00,000; fake parser outputs.

| Suite | Acceptance criteria covered |
|---|---|
| `test_auth.py` | login, OTP, wrong OTP, lockout after 5, reset, idle logout, landing |
| `test_vendor_onboarding.py` | invite→Active without email back-and-forth |
| `test_kyc_review.py` | per-doc review, activation gating, inviter ≠ activator |
| `test_invoice_upload_parse.py` | parse pre-fill, amber fields, dup/GSTIN/totals/size/type validations |
| `test_approval_routing.py` | ₹50k stops at L1; ₹15L → L3; queues + emails |
| `test_maker_checker.py` | no self-approval / repeat approval; bank change holds payment |
| `test_query_reject.py` | query returns to same level; reject final |
| `test_payments.py` | full/partial payments, vendor history, advice email |
| `test_vendor_isolation.py` | vendor A → B: every URL & file 404 |
| `test_reports_exports.py` | report counts, CSV/XLSX columns |
| `test_admin_config.py` | matrix change affects new invoices only; checklist; template toggle |
| `test_audit.py` | approval, status, bank, login in audit log |
| `test_smoke.py` (`@smoke`) | login, upload+parse, L1 approve |

Unit tests (`tests/unit/`) cover services (state machines, validations, routing),
permissions, RLS (raw SQL as vendor A sees no vendor B rows), crypto, parser rule checks.

## 9. Work breakdown

1. **core + accounts** — base model, settings, RLS op, permissions, crypto, auth/OTP/reset, seed. *(blocks all)*
2. **notify + audit** — templates, sender, task, bell, audit log/middleware. *(after 1)*
3. **parsing** — File, storage, scan, ParseJob, engines, rule checks. *(after 1)*
4. **vendors** — invite, registration, KYC upload/review, state machine, bank change, vendor users. *(after 2,3)*
5. **invoices** — upload/parse UI, validations, matrix, workflow, query thread, payments. *(after 4)*
6. **reports + admin console** — reports, exports, matrix/checklist/template/user admin, audit view. *(after 5)*
7. **infra** — Dockerfile, compose (+test), nginx, backup, CI. *(parallel from 1)*

Each module: builder writes models/services/views/templates + unit tests; tester writes
the Playwright suite from sections 4–5 in parallel.
