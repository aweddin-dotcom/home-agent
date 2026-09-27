import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import google_auth  # noqa: E402


def test_no_tool_can_send_except_send():
    for tool, scopes in google_auth.TOOLS.items():
        if tool != "send":
            assert google_auth.GMAIL_SEND not in scopes


def test_ingestion_is_read_only():
    assert set(google_auth.TOOLS["ingestion"]) == {
        google_auth.GMAIL_READONLY,
        google_auth.CALENDAR_READONLY,
    }


def test_missing_scopes():
    requested = [google_auth.GMAIL_READONLY, google_auth.CALENDAR_READONLY]
    assert google_auth.missing_scopes(requested, requested) == []
    assert google_auth.missing_scopes(requested, [google_auth.GMAIL_READONLY]) == [
        google_auth.CALENDAR_READONLY
    ]
    assert google_auth.missing_scopes(requested, None) == sorted(requested)


class FakeCreds:
    def __init__(self, granted):
        self.granted_scopes = granted

    def to_json(self):
        return '{"token": "fake"}'


class FakeFlow:
    granted = None

    @classmethod
    def from_client_config(cls, config, scopes):
        return cls()

    def run_local_server(self, **kwargs):
        assert kwargs.get("prompt") == "consent"
        return FakeCreds(FakeFlow.granted)


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "TOKENS_DIR", tmp_path / "tokens")
    monkeypatch.setattr(google_auth, "ROOT", tmp_path)
    monkeypatch.setattr(google_auth, "InstalledAppFlow", FakeFlow)
    monkeypatch.setattr(google_auth, "load_client_config", lambda: {"installed": {}})
    monkeypatch.setattr(
        google_auth.settings, "accounts", lambda include_disabled=False: {"gmail-test": {"provider": "google"}}
    )
    return tmp_path


def test_grant_saves_token_when_all_scopes_approved(fake_env):
    FakeFlow.granted = google_auth.TOOLS["ingestion"]
    google_auth.main(["grant", "gmail-test", "ingestion"])
    assert (fake_env / "tokens" / "gmail-test" / "ingestion" / "token.json").read_text() == '{"token": "fake"}'


def test_grant_saves_nothing_when_a_scope_is_unticked(fake_env):
    FakeFlow.granted = [google_auth.GMAIL_READONLY]
    with pytest.raises(SystemExit):
        google_auth.main(["grant", "gmail-test", "ingestion"])
    assert not (fake_env / "tokens" / "gmail-test" / "ingestion" / "token.json").exists()


def test_grant_refuses_unknown_or_non_google_accounts(fake_env, monkeypatch):
    monkeypatch.setattr(
        google_auth.settings, "accounts", lambda include_disabled=False: {"icloud": {"provider": "icloud"}}
    )
    for account in ("nope", "icloud"):
        with pytest.raises(SystemExit):
            google_auth.main(["grant", account, "ingestion"])
