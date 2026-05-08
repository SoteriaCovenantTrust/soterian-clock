"""
Unit tests for the Soterian Clock widget — the bits that don't need a Tk
display or live network. Run with `pytest tests/`.

Mocks `tkinter` before importing the module so the suite can run on a
headless CI runner without a DISPLAY. Other imports (requests, keyring,
PIL, pystray) are real but never invoked at import-time, so they're fine.

Covers:
  - _ver_tuple comparator (used by the version handshake + post-update notice)
  - _save_widget_token / _load_widget_token / _delete_widget_token (keyring
    with settings.json fallback + legacy migration)
  - _verify_tarball_sha256 (auto-update integrity check, including the
    graceful-degrade-on-404 path for legacy releases)
"""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock tkinter so module import works without DISPLAY on CI runners.
sys.modules.setdefault("tkinter", MagicMock())

# Repo layout: this file lives at tests/test_widget.py; widget is at the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soterian_clock as sc  # noqa: E402


# ============================================================================
# _ver_tuple
# ============================================================================

class TestVerTuple:
    def test_basic_semver(self):
        assert sc._ver_tuple("2.0.3") == (2, 0, 3)

    def test_two_part_version(self):
        assert sc._ver_tuple("2.1") == (2, 1)

    def test_empty_string(self):
        # _ver_tuple("") returns (0,) — single element tuple — so it
        # compares as less than anything starting with a positive int.
        assert sc._ver_tuple("") == (0,)
        assert sc._ver_tuple("") < sc._ver_tuple("0.1")

    def test_non_numeric_parts_become_zero(self):
        # We don't try to parse pre-release semver suffixes; non-numeric
        # parts compare as 0 so "2.0.3-beta" is treated as "2.0.0".
        assert sc._ver_tuple("2.0.3-beta") == (2, 0, 0)

    def test_numeric_ordering_not_lex(self):
        # Lexical ordering would put "2.10.0" before "2.2.0"; tuple
        # ordering of ints is the right answer.
        assert sc._ver_tuple("2.10.0") > sc._ver_tuple("2.2.0")

    def test_strict_greater_than(self):
        assert sc._ver_tuple("2.0.4") > sc._ver_tuple("2.0.3")
        assert sc._ver_tuple("3.0.0") > sc._ver_tuple("2.99.99")

    def test_equal_versions(self):
        assert sc._ver_tuple("2.0.3") == sc._ver_tuple("2.0.3")
        # Crucial for the post-update notice: don't trigger on identical.
        assert not (sc._ver_tuple("2.0.3") > sc._ver_tuple("2.0.3"))


# ============================================================================
# Token storage — keyring with settings.json fallback
# ============================================================================

@pytest.fixture
def mock_settings(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a tmp file so tests don't touch the
    real ~/.config/soterian-clock/settings.json."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(sc, "SETTINGS_PATH", settings_file)
    return settings_file


@pytest.fixture
def mock_keyring(monkeypatch):
    """In-memory dict pretending to be the OS keyring."""
    store = {}

    fake_kr = MagicMock()
    fake_kr.get_password.side_effect = lambda svc, user: store.get((svc, user))
    fake_kr.set_password.side_effect = lambda svc, user, val: store.update({(svc, user): val})

    def _del(svc, user):
        if (svc, user) not in store:
            raise Exception("not found")
        del store[(svc, user)]
    fake_kr.delete_password.side_effect = _del

    monkeypatch.setattr(sc, "_keyring_module", lambda: fake_kr)
    return store


class TestTokenStorage:
    def test_save_writes_to_keyring(self, mock_settings, mock_keyring):
        sc._save_widget_token("token-abc")
        assert mock_keyring[(sc._KEYRING_SERVICE, sc._KEYRING_USER)] == "token-abc"

    def test_save_scrubs_legacy_settings_token(self, mock_settings, mock_keyring):
        # Pre-existing legacy token in settings.json should be removed when
        # we save a new one to the keyring.
        mock_settings.write_text(json.dumps({"widget_token": "legacy", "alias": "X"}))
        sc._save_widget_token("token-new")
        on_disk = json.loads(mock_settings.read_text())
        assert "widget_token" not in on_disk
        assert on_disk["alias"] == "X"  # unrelated keys preserved

    def test_load_prefers_keyring(self, mock_settings, mock_keyring):
        mock_keyring[(sc._KEYRING_SERVICE, sc._KEYRING_USER)] = "from-keyring"
        # Even with a stale legacy entry, keyring wins.
        mock_settings.write_text(json.dumps({"widget_token": "stale-legacy"}))
        assert sc._load_widget_token() == "from-keyring"

    def test_load_migrates_legacy_then_returns(self, mock_settings, mock_keyring):
        # Empty keyring + legacy in settings → migrate + return.
        mock_settings.write_text(json.dumps({"widget_token": "legacy-tok"}))
        result = sc._load_widget_token()
        assert result == "legacy-tok"
        # After load, keyring has the value AND settings.json has been scrubbed.
        assert mock_keyring[(sc._KEYRING_SERVICE, sc._KEYRING_USER)] == "legacy-tok"
        assert "widget_token" not in json.loads(mock_settings.read_text())

    def test_load_returns_empty_when_nothing_anywhere(self, mock_settings, mock_keyring):
        assert sc._load_widget_token() == ""

    def test_delete_removes_from_both(self, mock_settings, mock_keyring):
        mock_keyring[(sc._KEYRING_SERVICE, sc._KEYRING_USER)] = "tok"
        mock_settings.write_text(json.dumps({"widget_token": "stale"}))
        sc._delete_widget_token()
        assert (sc._KEYRING_SERVICE, sc._KEYRING_USER) not in mock_keyring
        assert "widget_token" not in json.loads(mock_settings.read_text())

    def test_keyring_unavailable_falls_back(self, mock_settings, monkeypatch):
        # If keyring is None (sandbox without dbus), fall back to settings.json.
        monkeypatch.setattr(sc, "_keyring_module", lambda: None)
        sc._save_widget_token("fallback-tok")
        assert json.loads(mock_settings.read_text())["widget_token"] == "fallback-tok"
        assert sc._load_widget_token() == "fallback-tok"


# ============================================================================
# SHA256 verification for auto-update
# ============================================================================

class TestVerifyTarballSha256:
    def _make_clock(self):
        # We don't need a real SoterianClock instance; call the method
        # via an unbound-but-bound proxy that has the same signature.
        # The method is purely functional in our usage.
        cls = sc.SoterianClock
        # Create a minimal stand-in that has the method but doesn't
        # invoke __init__ (which needs a tk root).
        instance = cls.__new__(cls)
        return instance

    def _expected_sums_body(self, name: str, content: bytes) -> str:
        h = hashlib.sha256(content).hexdigest()
        return f"{h}  {name}\n"

    def test_verify_passes_on_match(self, tmp_path):
        content = b"test-tarball-bytes"
        tarball = tmp_path / "soterian-clock-9.9.9-linux-x86_64.tar.gz"
        tarball.write_bytes(content)
        sums_body = self._expected_sums_body(tarball.name, content)

        with patch.object(sc.requests, "get") as mock_get:
            resp = MagicMock(status_code=200, text=sums_body)
            resp.raise_for_status = lambda: None
            mock_get.return_value = resp

            ok, msg = self._make_clock()._verify_tarball_sha256(
                tarball, tarball.name, "https://example.invalid/SHA256SUMS")

        assert ok is True
        assert msg == ""

    def test_verify_fails_on_mismatch(self, tmp_path):
        tarball = tmp_path / "soterian-clock-9.9.9-linux-x86_64.tar.gz"
        tarball.write_bytes(b"actual-content")
        # SHA256SUMS lists the wrong hash for this file
        sums_body = "0000000000000000000000000000000000000000000000000000000000000000  " + tarball.name + "\n"

        with patch.object(sc.requests, "get") as mock_get:
            resp = MagicMock(status_code=200, text=sums_body)
            resp.raise_for_status = lambda: None
            mock_get.return_value = resp

            ok, msg = self._make_clock()._verify_tarball_sha256(
                tarball, tarball.name, "https://example.invalid/SHA256SUMS")

        assert ok is False
        assert "mismatch" in msg.lower()

    def test_verify_404_skips_gracefully(self, tmp_path):
        # Older releases predating the SHA256SUMS CI step return 404 on
        # the SHA256SUMS URL. Widget should log and proceed (return True)
        # so legacy auto-updates aren't broken.
        tarball = tmp_path / "soterian-clock-2.5.0-linux-x86_64.tar.gz"
        tarball.write_bytes(b"some-bytes")

        with patch.object(sc.requests, "get") as mock_get:
            resp = MagicMock(status_code=404)
            resp.raise_for_status.side_effect = Exception("should not be called for 404")
            mock_get.return_value = resp

            ok, msg = self._make_clock()._verify_tarball_sha256(
                tarball, tarball.name, "https://example.invalid/SHA256SUMS")

        assert ok is True
        assert msg == ""

    def test_verify_fails_when_filename_missing_from_sums(self, tmp_path):
        tarball = tmp_path / "soterian-clock-9.9.9-linux-x86_64.tar.gz"
        tarball.write_bytes(b"content")
        # SHA256SUMS exists but doesn't list our tarball
        other = "abcd" * 16 + "  some-other-file.tar.gz\n"

        with patch.object(sc.requests, "get") as mock_get:
            resp = MagicMock(status_code=200, text=other)
            resp.raise_for_status = lambda: None
            mock_get.return_value = resp

            ok, msg = self._make_clock()._verify_tarball_sha256(
                tarball, tarball.name, "https://example.invalid/SHA256SUMS")

        assert ok is False
        assert "missing entry" in msg.lower()

    def test_verify_handles_starred_format(self, tmp_path):
        # Some sha256sum implementations (e.g. busybox in binary mode) emit
        # "<hex> *<filename>" with a leading asterisk. Should still parse.
        content = b"binary-mode-bytes"
        tarball = tmp_path / "soterian-clock-9.9.9-linux-x86_64.tar.gz"
        tarball.write_bytes(content)
        h = hashlib.sha256(content).hexdigest()
        sums_body = f"{h}  *{tarball.name}\n"

        with patch.object(sc.requests, "get") as mock_get:
            resp = MagicMock(status_code=200, text=sums_body)
            resp.raise_for_status = lambda: None
            mock_get.return_value = resp

            ok, msg = self._make_clock()._verify_tarball_sha256(
                tarball, tarball.name, "https://example.invalid/SHA256SUMS")

        assert ok is True
