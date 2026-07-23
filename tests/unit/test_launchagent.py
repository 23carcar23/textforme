"""Tests for launchagent module. Uses mocked subprocess.run and temp paths."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from textforme import config, launchagent


class TestGetTextformedPath:
    """Test launchagent._get_textformed_path()."""

    def test_get_textformed_path_via_shutil_which(self):
        """Try to find textformed via shutil.which first."""
        with patch("textforme.launchagent.shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/textformed"
            result = launchagent._get_textformed_path()
            assert result == "/usr/local/bin/textformed"
            mock_which.assert_called_once_with("textformed")

    def test_get_textformed_path_fallback_to_sys_executable(self):
        """Fallback to sys.executable/.parent/textformed if shutil.which fails."""
        import sys as sys_module
        with patch("textforme.launchagent.shutil.which") as mock_which:
            with patch.object(sys_module, "executable", "/opt/python/bin/python"):
                mock_which.return_value = None
                result = launchagent._get_textformed_path()
                # Result should be /opt/python/bin/textformed
                assert "textformed" in result
                assert "bin" in result


class TestInstall:
    """Test launchagent.install()."""

    def test_install_writes_plist(self, tmp_path):
        """Write the rendered plist to LAUNCH_AGENT_PATH."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                            mock_path.return_value = "/usr/local/bin/textformed"
                            launchagent.install()

                            # Verify plist was written
                            assert plist_path.exists()
                            content = plist_path.read_text()
                            assert "/usr/local/bin/textformed" in content
                            assert str(log_dir) in content

    def test_install_substitutes_placeholders(self, tmp_path):
        """Replace {TEXTFORMED_PATH} and {LOG_DIR} placeholders."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"
        textformed_path = "/opt/bin/textformed"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                            mock_path.return_value = textformed_path
                            launchagent.install()

                            content = plist_path.read_text()
                            # Verify placeholders are replaced
                            assert "{TEXTFORMED_PATH}" not in content
                            assert "{LOG_DIR}" not in content
                            assert textformed_path in content
                            assert str(log_dir) in content

    def test_install_calls_ensure_dirs(self, tmp_path):
        """Call config.ensure_dirs() during install."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs") as mock_ensure:
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                            mock_path.return_value = "/usr/local/bin/textformed"
                            launchagent.install()
                            mock_ensure.assert_called_once()

    def test_install_calls_launchctl_bootout_then_bootstrap(self, tmp_path):
        """Call launchctl bootout (ignore failure) then bootstrap."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            with patch("textforme.launchagent.os.getuid") as mock_uid:
                                mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                                mock_path.return_value = "/usr/local/bin/textformed"
                                mock_uid.return_value = 501
                                launchagent.install()

                                # Verify bootout and bootstrap calls
                                calls = mock_run.call_args_list
                                assert len(calls) == 2
                                # First call: bootout
                                assert calls[0][0][0] == ["launchctl", "bootout", "gui/501", str(plist_path)]
                                # Second call: bootstrap
                                assert calls[1][0][0] == ["launchctl", "bootstrap", "gui/501", str(plist_path)]

    def test_install_raises_when_bootstrap_fails_and_not_running(self, tmp_path):
        """bootstrap failing AND the job not being loaded afterward raises."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            with patch("textforme.launchagent.os.getuid") as mock_uid:
                                with patch("textforme.launchagent.is_running") as mock_is_running:
                                    mock_path.return_value = "/usr/local/bin/textformed"
                                    mock_uid.return_value = 501
                                    mock_is_running.return_value = False
                                    # bootout succeeds, bootstrap fails
                                    mock_run.side_effect = [
                                        Mock(returncode=0, stdout="", stderr=""),
                                        Mock(returncode=1, stdout="", stderr="Bootstrap failed: 5: Input/output error"),
                                    ]

                                    with pytest.raises(launchagent.LaunchAgentError) as excinfo:
                                        launchagent.install()

                                    assert "bootstrap" in str(excinfo.value)
                                    assert "Input/output error" in str(excinfo.value)

    def test_install_does_not_raise_when_bootstrap_fails_but_job_is_loaded(self, tmp_path):
        """bootstrap reporting failure (e.g. 'already bootstrapped') is
        tolerated as long as the job is actually loaded afterward."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            with patch("textforme.launchagent.os.getuid") as mock_uid:
                                with patch("textforme.launchagent.is_running") as mock_is_running:
                                    mock_path.return_value = "/usr/local/bin/textformed"
                                    mock_uid.return_value = 501
                                    mock_is_running.return_value = True
                                    mock_run.side_effect = [
                                        Mock(returncode=0, stdout="", stderr=""),
                                        Mock(returncode=37, stdout="", stderr="Bootstrap failed: 37: Already bootstrapped"),
                                    ]

                                    # Should not raise
                                    launchagent.install()

    def test_install_idempotent(self, tmp_path):
        """install() can be called multiple times safely."""
        plist_path = tmp_path / "test.plist"
        log_dir = tmp_path / "logs"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.config.LOG_DIR", log_dir):
                with patch("textforme.launchagent.config.ensure_dirs"):
                    with patch("textforme.launchagent._get_textformed_path") as mock_path:
                        with patch("textforme.launchagent.subprocess.run") as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                            mock_path.return_value = "/usr/local/bin/textformed"
                            # Call install twice
                            launchagent.install()
                            first_content = plist_path.read_text()
                            launchagent.install()
                            second_content = plist_path.read_text()
                            # Content should be identical
                            assert first_content == second_content


class TestUninstall:
    """Test launchagent.uninstall()."""

    def test_uninstall_calls_stop(self, tmp_path):
        """Call stop() during uninstall."""
        plist_path = tmp_path / "test.plist"
        plist_path.write_text("<plist/>")

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.stop") as mock_stop:
                launchagent.uninstall()
                mock_stop.assert_called_once()

    def test_uninstall_removes_plist(self, tmp_path):
        """Remove the plist file."""
        plist_path = tmp_path / "test.plist"
        plist_path.write_text("<plist/>")

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.stop"):
                launchagent.uninstall()
                # Plist should be deleted
                assert not plist_path.exists()

    def test_uninstall_idempotent_missing_plist(self, tmp_path):
        """uninstall() doesn't raise if plist is missing."""
        plist_path = tmp_path / "nonexistent.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.stop"):
                # Should not raise
                launchagent.uninstall()


class TestIsInstalled:
    """Test launchagent.is_installed()."""

    def test_is_installed_true_when_plist_exists(self, tmp_path):
        """Return True if plist file exists."""
        plist_path = tmp_path / "test.plist"
        plist_path.write_text("<plist/>")

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            assert launchagent.is_installed() is True

    def test_is_installed_false_when_plist_missing(self, tmp_path):
        """Return False if plist file doesn't exist."""
        plist_path = tmp_path / "nonexistent.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            assert launchagent.is_installed() is False


class TestIsRunning:
    """Test launchagent.is_running()."""

    def test_is_running_true_when_pid_present(self):
        """Return True if launchctl print shows 'pid = ' and returncode is 0."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                mock_uid.return_value = 501
                mock_run.return_value = Mock(
                    returncode=0,
                    stdout="    pid = 12345\n    state = running",
                )
                result = launchagent.is_running()
                assert result is True
                # Verify launchctl print was called with correct args
                mock_run.assert_called_once_with(
                    ["launchctl", "print", "gui/501/com.textforme.daemon"],
                    capture_output=True,
                    text=True,
                )

    def test_is_running_false_when_no_pid(self):
        """Return False if 'pid = ' is not in stdout."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                mock_uid.return_value = 501
                mock_run.return_value = Mock(returncode=0, stdout="state = not running")
                result = launchagent.is_running()
                assert result is False

    def test_is_running_false_when_nonzero_returncode(self):
        """Return False if launchctl print returns non-zero."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                mock_uid.return_value = 501
                mock_run.return_value = Mock(returncode=1, stdout="")
                result = launchagent.is_running()
                assert result is False

    def test_is_running_false_on_exception(self):
        """Return False if subprocess raises an exception."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                mock_uid.return_value = 501
                mock_run.side_effect = Exception("Unexpected error")
                result = launchagent.is_running()
                assert result is False


class TestStart:
    """Test launchagent.start()."""

    def test_start_always_installs_to_rebootstrap(self, tmp_path):
        """start() must always (re)install: stop() boots the job out of
        launchd, and kickstarting an unloaded job is a no-op — so a bare
        kickstart after stop() would leave the service dead."""
        plist_path = tmp_path / "test.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.install") as mock_install:
                with patch("textforme.launchagent.subprocess.run") as mock_run:
                    with patch("textforme.launchagent.os.getuid") as mock_uid:
                        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                        mock_uid.return_value = 501
                        launchagent.start()
                        mock_install.assert_called_once()

    def test_start_calls_kickstart(self, tmp_path):
        """Call launchctl kickstart after installing."""
        plist_path = tmp_path / "test.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.install"):
                with patch("textforme.launchagent.subprocess.run") as mock_run:
                    with patch("textforme.launchagent.os.getuid") as mock_uid:
                        mock_uid.return_value = 501
                        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                        launchagent.start()
                        # Verify kickstart was called
                        mock_run.assert_called_once_with(
                            ["launchctl", "kickstart", "gui/501/com.textforme.daemon"],
                            capture_output=True,
                            text=True,
                        )

    def test_start_raises_when_kickstart_fails_and_not_running(self, tmp_path):
        """kickstart failing AND the job not being running afterward raises."""
        plist_path = tmp_path / "test.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.install"):
                with patch("textforme.launchagent.subprocess.run") as mock_run:
                    with patch("textforme.launchagent.os.getuid") as mock_uid:
                        with patch("textforme.launchagent.is_running") as mock_is_running:
                            mock_uid.return_value = 501
                            mock_is_running.return_value = False
                            mock_run.return_value = Mock(returncode=1, stdout="", stderr="No such process")
                            with pytest.raises(launchagent.LaunchAgentError) as excinfo:
                                launchagent.start()
                            assert "kickstart" in str(excinfo.value)
                            assert "No such process" in str(excinfo.value)

    def test_start_does_not_raise_when_kickstart_fails_but_running(self, tmp_path):
        """kickstart reporting failure is tolerated if the job is actually running."""
        plist_path = tmp_path / "test.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.install"):
                with patch("textforme.launchagent.subprocess.run") as mock_run:
                    with patch("textforme.launchagent.os.getuid") as mock_uid:
                        with patch("textforme.launchagent.is_running") as mock_is_running:
                            mock_uid.return_value = 501
                            mock_is_running.return_value = True
                            mock_run.return_value = Mock(returncode=1, stdout="", stderr="some transient error")
                            # Should not raise
                            launchagent.start()

    def test_start_propagates_install_failure(self, tmp_path):
        """A LaunchAgentError from install() propagates out of start()."""
        plist_path = tmp_path / "test.plist"

        with patch("textforme.launchagent.config.LAUNCH_AGENT_PATH", plist_path):
            with patch("textforme.launchagent.install") as mock_install:
                mock_install.side_effect = launchagent.LaunchAgentError("bootstrap failed")
                with pytest.raises(launchagent.LaunchAgentError):
                    launchagent.start()


class TestStop:
    """Test launchagent.stop()."""

    def test_stop_calls_launchctl_bootout(self):
        """Call launchctl bootout to unload the daemon."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                mock_uid.return_value = 501
                mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                launchagent.stop()
                mock_run.assert_called_once_with(
                    ["launchctl", "bootout", "gui/501/com.textforme.daemon"],
                    capture_output=True,
                    text=True,
                )

    def test_stop_ignores_failure_when_job_not_running(self):
        """stop() doesn't raise if bootout fails but the job isn't running
        afterward (e.g. it was already unloaded — a legitimate failure)."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                with patch("textforme.launchagent.is_running") as mock_is_running:
                    mock_uid.return_value = 501
                    mock_is_running.return_value = False
                    mock_run.return_value = Mock(returncode=1, stdout="", stderr="No such process")
                    # Should not raise
                    launchagent.stop()

    def test_stop_raises_when_bootout_fails_and_job_still_running(self):
        """stop() raises if bootout fails and the job is still running
        afterward — a real failure, not just 'wasn't loaded'."""
        with patch("textforme.launchagent.subprocess.run") as mock_run:
            with patch("textforme.launchagent.os.getuid") as mock_uid:
                with patch("textforme.launchagent.is_running") as mock_is_running:
                    mock_uid.return_value = 501
                    mock_is_running.return_value = True
                    mock_run.return_value = Mock(returncode=1, stdout="", stderr="Operation not permitted")
                    with pytest.raises(launchagent.LaunchAgentError) as excinfo:
                        launchagent.stop()
                    assert "bootout" in str(excinfo.value)
                    assert "Operation not permitted" in str(excinfo.value)


class TestPlistTemplate:
    """Test the embedded plist template."""

    def test_plist_template_contains_required_keys(self):
        """Verify the plist template has required keys."""
        template = launchagent.PLIST_TEMPLATE
        assert "com.textforme.daemon" in template
        assert "RunAtLoad" in template
        assert "KeepAlive" in template
        assert "{TEXTFORMED_PATH}" in template
        assert "{LOG_DIR}" in template
        assert "daemon.out.log" in template
        assert "daemon.err.log" in template

    def test_plist_template_valid_xml_after_substitution(self, tmp_path):
        """Verify rendered plist is valid XML."""
        import xml.etree.ElementTree as ET

        template = launchagent.PLIST_TEMPLATE
        rendered = template.format(
            TEXTFORMED_PATH="/usr/local/bin/textformed",
            LOG_DIR="/var/log/textforme",
        )

        # Should parse as valid XML
        try:
            ET.fromstring(rendered)
        except ET.ParseError as e:
            pytest.fail(f"Rendered plist is not valid XML: {e}")
