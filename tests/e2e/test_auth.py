"""Auth: login + OTP, wrong OTP, lockout after 5 failures, password reset, idle logout, role landing."""
import re

from django.conf import settings
from django.core import mail
from playwright.sync_api import expect

from .conftest import PW, emails_to, last_otp, login, logout


def _password_step(page, email, password=PW):
    page.goto("/login/")
    page.get_by_test_id("login-email").fill(email)
    page.get_by_test_id("login-password").fill(password)
    page.get_by_test_id("login-submit-btn").click()


def _link_path(message):
    """The deep link in an email points at SITE_URL; return just its path so we can open it on the live server."""
    site = settings.SITE_URL.rstrip("/")
    body = message.body + "".join(c for c, _ in getattr(message, "alternatives", []))
    m = re.search(re.escape(site) + r"(/[^\s\"'<>]*)", body)
    assert m, f"No {site} link in email {message.subject!r}"
    return m.group(1)


def test_login_requires_otp_then_lands_on_vendor_portal(page, seed):
    _password_step(page, "vendora@test.local")
    expect(page.get_by_test_id("otp-code")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/login/otp/$"))
    # No session yet: the portal still bounces to login.
    assert len(emails_to("vendora@test.local", "sign-in code")) == 1
    page.get_by_test_id("otp-code").fill(last_otp("vendora@test.local"))
    page.get_by_test_id("otp-submit-btn").click()
    expect(page.get_by_test_id("current-user")).to_contain_text("V00001")
    expect(page).to_have_url(re.compile(r"/vendor/$"))
    expect(page.get_by_test_id("vendor-dashboard")).to_be_visible()


def test_wrong_password_shows_login_error(page, seed):
    _password_step(page, "vendora@test.local", "not-the-password")
    expect(page.get_by_test_id("login-error")).to_be_visible()
    expect(page.get_by_test_id("otp-code")).to_have_count(0)
    assert emails_to("vendora@test.local", "sign-in code") == []


def test_wrong_otp_shows_otp_error_and_does_not_sign_in(page, seed):
    _password_step(page, "l1@test.local")
    page.get_by_test_id("otp-code").wait_for()
    code = last_otp("l1@test.local")
    wrong = "000000" if code != "000000" else "111111"
    page.get_by_test_id("otp-code").fill(wrong)
    page.get_by_test_id("otp-submit-btn").click()
    expect(page.get_by_test_id("otp-error")).to_be_visible()
    expect(page.get_by_test_id("current-user")).to_have_count(0)
    # The right code still works afterwards.
    page.get_by_test_id("otp-code").fill(code)
    page.get_by_test_id("otp-submit-btn").click()
    expect(page.get_by_test_id("current-user")).to_have_text("Lina L1")


def test_lockout_after_five_failed_passwords(page, seed):
    for _ in range(4):
        _password_step(page, "vendora@test.local", "wrong-password")
        expect(page.get_by_test_id("login-error")).to_be_visible()
    _password_step(page, "vendora@test.local", "wrong-password")
    expect(page.get_by_test_id("account-locked")).to_be_visible()
    # Locked: even the correct password is refused and no OTP is sent.
    _password_step(page, "vendora@test.local", PW)
    expect(page.get_by_test_id("account-locked")).to_be_visible()
    assert emails_to("vendora@test.local", "sign-in code") == []
    # Lockout is per account: another user can still sign in.
    login(page, "vendorb@test.local")
    expect(page.get_by_test_id("vendor-dashboard")).to_be_visible()


def test_password_reset_via_emailed_link(page, seed):
    page.goto("/login/")
    page.get_by_test_id("forgot-password-link").click()
    page.get_by_test_id("reset-email").fill("vendora@test.local")
    page.get_by_test_id("reset-submit-btn").click()
    expect(page.get_by_test_id("reset-sent")).to_be_visible()

    msgs = emails_to("vendora@test.local", "Reset your")
    assert len(msgs) == 1
    page.goto(_link_path(msgs[0]))
    new_pw = "N3w-Passw0rd!xyz"
    page.get_by_test_id("new-password1").fill(new_pw)
    page.get_by_test_id("new-password2").fill(new_pw)
    page.get_by_test_id("reset-confirm-btn").click()
    expect(page.get_by_test_id("reset-complete")).to_be_visible()

    # Old password no longer works; the new one does.
    _password_step(page, "vendora@test.local", PW)
    expect(page.get_by_test_id("login-error")).to_be_visible()
    login(page, "vendora@test.local", new_pw)
    expect(page.get_by_test_id("vendor-dashboard")).to_be_visible()

    # The link is single-use.
    logout(page)
    page.goto(_link_path(msgs[0]))
    expect(page.get_by_test_id("reset-invalid")).to_be_visible()


def test_password_reset_for_unknown_email_sends_nothing(page, seed):
    page.goto("/password-reset/")
    page.get_by_test_id("reset-email").fill("nobody@test.local")
    page.get_by_test_id("reset-submit-btn").click()
    expect(page.get_by_test_id("reset-sent")).to_be_visible()
    assert [m for m in mail.outbox if "Reset your" in m.subject] == []


def test_idle_logout_redirects_to_login(page, seed):
    from django.contrib.sessions.models import Session

    login(page, "vendora@test.local")
    page.goto("/vendor/invoices/")
    expect(page.get_by_test_id("current-user")).to_be_visible()
    # Simulate the idle timeout expiring server-side.
    Session.objects.all().delete()
    page.reload()
    expect(page).to_have_url(re.compile(r"/login/"))
    expect(page.get_by_test_id("login-email")).to_be_visible()
    expect(page.get_by_test_id("current-user")).to_have_count(0)


def test_logout_ends_session(page, seed):
    login(page, "finance@test.local")
    logout(page)
    page.goto("/console/")
    expect(page).to_have_url(re.compile(r"/login/"))


def test_role_based_landing_vendor(page, seed):
    login(page, "vendora.user@test.local")
    expect(page).to_have_url(re.compile(r"/vendor/$"))
    expect(page.get_by_test_id("vendor-dashboard")).to_be_visible()
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/vendor/$"))


def test_role_based_landing_internal(page, seed):
    for email in ("l1@test.local", "finance@test.local", "vm@test.local"):
        login(page, email)
        expect(page).to_have_url(re.compile(r"/console/$"))
        expect(page.get_by_test_id("queue-table")).to_be_visible()
        page.goto("/")
        expect(page).to_have_url(re.compile(r"/console/$"))
        logout(page)
