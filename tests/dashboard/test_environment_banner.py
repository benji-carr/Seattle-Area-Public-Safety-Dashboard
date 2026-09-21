import pytest

import app as app_module


def test_staging_environment_banner(monkeypatch):
    monkeypatch.setattr(app_module, "APP_ENV", "staging")

    banner = app_module.make_environment_banner()

    assert banner.children == "STAGING ENVIRONMENT"
    assert banner.className == "staging-banner"


@pytest.mark.parametrize("environment", ["production", "development", ""])
def test_environment_banner_absent_outside_staging(monkeypatch, environment):
    monkeypatch.setattr(app_module, "APP_ENV", environment)

    assert app_module.make_environment_banner() is None
