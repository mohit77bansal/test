"""Remote post-deploy smoke (spec Stage 3 step 4): runs against a deployed URL, no Django test DB.

    SMOKE_BASE_URL=https://vendor-uat.solfin.co.in \
    SMOKE_VENDOR_EMAIL=... SMOKE_FINANCE_EMAIL=... SMOKE_L1_EMAIL=... SMOKE_PASSWORD=... \
    pytest tests/smoke -m smoke -p no:django

Use dedicated smoke accounts with `mfa_enabled = False` (OTP mail cannot be read remotely), an active
smoke vendor, and a cost centre whose L1 owner is the smoke L1 user.
"""
import os

import pytest


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture(scope="session")
def base_url():
    return env("SMOKE_BASE_URL")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "ignore_https_errors": os.environ.get("SMOKE_INSECURE_TLS") == "1"}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return {**browser_type_launch_args, **({"executable_path": exe} if exe else {})}
