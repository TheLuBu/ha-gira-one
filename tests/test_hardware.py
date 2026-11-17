"""Verify that hardware-test bootstrap cannot replace existing HA configuration."""

import tempfile
import unittest
from pathlib import Path

from cryptography import x509

from dev.start_hardware import prepare_config


class HardwareBootstrapTests(unittest.TestCase):
    """Keep the hardware test's config, TLS identity and client identity separate."""

    def setUp(self) -> None:
        """Use a fresh directory for every check."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name)
        self.url = "https://192.0.2.20:18124"

    def test_restart_preserves_configuration_and_private_key(self) -> None:
        """Restarting the test must not replace its persistent files."""
        prepare_config(self.config, self.url)
        key = (self.config / "tls/key.pem").read_bytes()
        configuration = self.config / "configuration.yaml"
        configuration.write_text("# test user settings\n", encoding="utf-8")
        prepare_config(self.config, self.url)
        self.assertEqual(
            configuration.read_text(encoding="utf-8"), "# test user settings\n"
        )
        self.assertEqual((self.config / "tls/key.pem").read_bytes(), key)
        certificate = x509.load_pem_x509_certificate(
            (self.config / "tls/cert.pem").read_bytes()
        )
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        self.assertIn(
            "192.0.2.20",
            [str(address) for address in names.get_values_for_type(x509.IPAddress)],
        )

    def test_existing_ha_configuration_is_rejected(self) -> None:
        """Even accidental mounting of another HA config must fail closed."""
        configuration = self.config / "configuration.yaml"
        configuration.write_text("# existing config\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Refusing"):
            prepare_config(self.config, self.url)
        self.assertEqual(
            configuration.read_text(encoding="utf-8"), "# existing config\n"
        )
        self.assertFalse((self.config / "tls").exists())

    def test_existing_storage_directory_is_rejected(self) -> None:
        """Never initialize over another HA instance's storage."""
        (self.config / ".storage").mkdir()
        with self.assertRaisesRegex(ValueError, "Refusing"):
            prepare_config(self.config, self.url)

    def test_invalid_callback_urls_do_not_create_files(self) -> None:
        """Reject origins the real Gira server cannot use."""
        for url in (
            "",
            "http://192.0.2.20",
            "https://localhost",
            "https://127.0.0.1",
            "https://0.0.0.0",
            "https://user:password@host",
            "https://host/path",
            "https://host:0",
            "https://host?query=1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                prepare_config(self.config, url)
        self.assertEqual(list(self.config.iterdir()), [])

    def test_changed_url_or_partial_certificate_is_rejected(self) -> None:
        """Refuse changes that would silently invalidate stored HTTP settings."""
        prepare_config(self.config, self.url)
        with self.assertRaisesRegex(ValueError, "Callback URL changed"):
            prepare_config(self.config, "https://192.0.2.21:18124")
        (self.config / "tls/key.pem").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            prepare_config(self.config, self.url)
