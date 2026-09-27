import sys
from pathlib import Path

import keyring
import keyring.backend
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import secrets_cli  # noqa: E402


class MemoryKeyring(keyring.backend.KeyringBackend):
    """Keeps secrets in memory so tests never touch the real credential store."""

    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if self.store.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture
def env(tmp_path, monkeypatch):
    registry = tmp_path / "secrets.yaml"
    registry.write_text(
        "secrets:\n"
        "  alpha:\n    description: first\n"
        "  beta:\n    description: second\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_cli, "REGISTRY", registry)
    monkeypatch.setattr(secrets_cli, "OUT_DIR", tmp_path / "out")
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield tmp_path, backend
    keyring.set_keyring(previous)


def prompt_with(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr(secrets_cli.getpass, "getpass", lambda prompt: next(replies))


def test_set_prompts_twice_and_stores(env, monkeypatch):
    _, backend = env
    prompt_with(monkeypatch, "s3cret", "s3cret")
    secrets_cli.main(["set", "alpha"])
    assert backend.store[("home-agent", "alpha")] == "s3cret"


def test_set_rejects_mismatch(env, monkeypatch):
    _, backend = env
    prompt_with(monkeypatch, "one", "two")
    with pytest.raises(SystemExit):
        secrets_cli.main(["set", "alpha"])
    assert backend.store == {}


def test_set_rejects_unknown_name(env, monkeypatch):
    prompt_with(monkeypatch, "x", "x")
    with pytest.raises(SystemExit):
        secrets_cli.main(["set", "not-in-registry"])


def test_set_from_file_strips_whitespace(env):
    tmp_path, backend = env
    source = tmp_path / "client.json"
    source.write_text('{"client_id": "abc"}\n', encoding="utf-8")
    secrets_cli.main(["set", "alpha", "--from-file", str(source)])
    assert backend.store[("home-agent", "alpha")] == '{"client_id": "abc"}'


def test_status_never_prints_values(env, capsys):
    _, backend = env
    backend.store[("home-agent", "alpha")] = "s3cret"
    secrets_cli.main(["status"])
    out = capsys.readouterr().out
    assert "s3cret" not in out
    assert "alpha" in out and "set" in out
    assert "beta" in out and "missing" in out


def test_export_writes_set_secrets_and_clears_removed_ones(env):
    tmp_path, backend = env
    out = tmp_path / "out"
    backend.store[("home-agent", "alpha")] = "a-value"
    backend.store[("home-agent", "beta")] = "b-value"
    secrets_cli.main(["export"])
    assert (out / "alpha").read_text(encoding="utf-8") == "a-value"
    assert (out / "beta").read_text(encoding="utf-8") == "b-value"

    del backend.store[("home-agent", "beta")]
    secrets_cli.main(["export"])
    assert (out / "alpha").exists()
    assert not (out / "beta").exists()


def test_remove_deletes_from_store_and_export(env):
    tmp_path, backend = env
    backend.store[("home-agent", "alpha")] = "a-value"
    secrets_cli.main(["export"])
    secrets_cli.main(["remove", "alpha"])
    assert ("home-agent", "alpha") not in backend.store
    assert not (tmp_path / "out" / "alpha").exists()
