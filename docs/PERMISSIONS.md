# Permission matrix

Generated from `apps/core/permissions.py` by `python manage.py permission_matrix`. Do not edit by hand.

Object-level rules on top of this table: vendor users only ever see their own vendor (queryset + RLS); nobody approves an invoice they uploaded or already acted on at an earlier level; the vendor's inviter cannot approve its KYC or activate it; a bank change cannot be approved by its requester.

| Permission | Action | VENDOR_ADMIN | VENDOR_USER | VENDOR_MANAGER | L1 | L2 | L3 | FINANCE | ADMIN | AUDITOR |
|---|---|---|---|---|---|---|---|---|---|---|
| `vendor.edit_profile` | Edit vendor profile and KYC | ✓ |  |  |  |  |  |  |  |  |
| `vendor.manage_users` | Add/remove vendor users | ✓ |  |  |  |  |  |  |  |  |
| `vendor.edit_bank` | Request bank detail change | ✓ |  |  |  |  |  |  |  |  |
| `invoice.upload` | Upload and submit invoices | ✓ | ✓ |  |  |  |  |  |  |  |
| `invoice.reply` | Reply to invoice queries | ✓ | ✓ |  |  |  |  |  |  |  |
| `invoice.view_own` | View own invoices and payments | ✓ | ✓ |  |  |  |  |  |  |  |
| `vendor.view_all` | View all vendors |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `vendor.invite` | Invite vendors |  |  | ✓ |  |  |  |  |  |  |
| `vendor.review_kyc` | Review KYC documents, send back |  |  | ✓ |  |  |  |  |  |  |
| `vendor.activate` | Activate / reject vendors |  |  | ✓ |  |  |  |  |  |  |
| `vendor.block` | Block / unblock vendors |  |  | ✓ |  |  |  |  |  |  |
| `vendor.verify_gstin` | Verify a changed GSTIN |  |  | ✓ |  |  |  |  |  |  |
| `invoice.view_all` | View all invoices |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `invoice.fin_precheck` | Finance pre-check |  |  |  |  |  |  | ✓ |  |  |
| `invoice.approve_l1` | Approve / reject / query at L1 |  |  |  | ✓ |  |  |  |  |  |
| `invoice.approve_l2` | Approve / reject / query at L2 |  |  |  |  | ✓ |  |  |  |  |
| `invoice.approve_l3` | Approve / reject / query at L3 |  |  |  |  |  | ✓ |  |  |  |
| `payment.record` | Record payments (UTR, TDS) |  |  |  |  |  |  | ✓ |  |  |
| `bank.approve` | Approve bank-detail changes |  |  |  |  |  |  | ✓ |  |  |
| `report.view` | View reports |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `report.export` | Export CSV / Excel |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `file.view` | Download vendor files |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `admin.users` | Manage internal users and roles |  |  |  |  |  |  |  | ✓ |  |
| `admin.matrix` | Edit approval matrix |  |  |  |  |  |  |  | ✓ |  |
| `admin.checklist` | Edit document checklist |  |  |  |  |  |  |  | ✓ |  |
| `admin.templates` | Switch email templates on/off |  |  |  |  |  |  |  | ✓ |  |
| `audit.view` | View audit log |  |  |  |  |  |  |  | ✓ | ✓ |
