"""Create disposable certificates for the isolated development network."""

import datetime as dt
import ipaddress
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def create_tls_context(
    directory: Path,
    hosts: tuple[str, ...] = ("localhost", "gira", "homeassistant", "127.0.0.1"),
) -> ssl.SSLContext:
    """Write a temporary self-signed certificate; never use it in production."""
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gira development")])
    now = dt.datetime.now(dt.UTC)
    alternative_names = []
    for host in hosts:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            alternative_names.append(x509.DNSName(host))
        else:
            alternative_names.append(x509.IPAddress(address))
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName(alternative_names),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "key.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    certificate_path = directory / "cert.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    return context
