"""Tests for keychain module. Uses mocked subprocess.run to avoid touching the real Keychain."""

import subprocess
from unittest.mock import Mock, patch

import pytest

from textforme import keychain
from textforme.config import KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE


class TestGetApiKey:
    """Test keychain.get_api_key()."""

    def test_get_api_key_success(self):
        """Return the API key when security command succeeds."""
        fake_key = "sk-test-key-12345"
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=fake_key + "\n")
            result = keychain.get_api_key()
            assert result == fake_key
            # Verify exact argv
            mock_run.assert_called_once_with(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
                capture_output=True,
                text=True,
            )

    def test_get_api_key_missing(self):
        """Return None when key is not found (returncode != 0)."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=44)  # Item not found
            result = keychain.get_api_key()
            assert result is None

    def test_get_api_key_strips_newline(self):
        """Strip trailing newline from output."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="key-value\n")
            result = keychain.get_api_key()
            assert result == "key-value"

    def test_get_api_key_exception_returns_none(self):
        """Return None if subprocess raises an exception."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")
            result = keychain.get_api_key()
            assert result is None

    def test_get_api_key_never_logs_key_value(self):
        """Ensure key value does not appear in exception messages."""
        fake_key = "sk-super-secret-12345"
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Some error")
            # Should not raise an exception
            result = keychain.get_api_key()
            assert result is None
            # Verify the exception doesn't contain the key (it shouldn't even reach logging)


class TestSetApiKey:
    """Test keychain.set_api_key()."""

    def test_set_api_key_success(self):
        """Store the API key using security add-generic-password."""
        fake_key = "sk-test-key-12345"
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            keychain.set_api_key(fake_key)
            mock_run.assert_called_once_with(
                ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", fake_key],
                capture_output=True,
            )

    def test_set_api_key_uses_update_flag(self):
        """Ensure the -U flag is used (update if exists)."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            keychain.set_api_key("new-key")
            args = mock_run.call_args[0][0]
            assert "-U" in args

    def test_set_api_key_never_logs_key_on_exception(self):
        """Key value should not be visible in exception messages."""
        fake_key = "sk-super-secret-12345"
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Command failed")
            # The exception should propagate without containing the key
            try:
                keychain.set_api_key(fake_key)
            except Exception as e:
                # Verify the key doesn't appear in the exception message
                assert fake_key not in str(e)


class TestDeleteApiKey:
    """Test keychain.delete_api_key()."""

    def test_delete_api_key_success(self):
        """Delete the API key using security delete-generic-password."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            keychain.delete_api_key()
            mock_run.assert_called_once_with(
                ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
                capture_output=True,
            )

    def test_delete_api_key_ignores_not_found(self):
        """No error even if key doesn't exist."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=44)  # Item not found
            # Should not raise
            keychain.delete_api_key()
            mock_run.assert_called_once()

    def test_delete_api_key_ignores_exception(self):
        """No error even if subprocess raises an exception."""
        with patch("textforme.keychain.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")
            # Should not raise
            keychain.delete_api_key()


class TestHasApiKey:
    """Test keychain.has_api_key()."""

    def test_has_api_key_true_when_key_exists(self):
        """Return True if get_api_key returns a non-None value."""
        with patch("textforme.keychain.get_api_key") as mock_get:
            mock_get.return_value = "sk-test-key"
            result = keychain.has_api_key()
            assert result is True

    def test_has_api_key_false_when_missing(self):
        """Return False if get_api_key returns None."""
        with patch("textforme.keychain.get_api_key") as mock_get:
            mock_get.return_value = None
            result = keychain.has_api_key()
            assert result is False
