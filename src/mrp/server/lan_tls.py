"""Private certificate authority and TLS material for the LAN listener.

The only downloadable certificate is the public root certificate. Signing keys
remain under ``<MRP_DATA_ROOT>/security/lan-tls`` and are never returned by an
HTTP route or included in status data.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import uuid

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

UTC = timezone.utc
CA_LIFETIME_DAYS = 5 * 365
LEAF_LIFETIME_DAYS = 90
LEAF_RENEW_BEFORE = timedelta(days=15)
CA_RENEW_BLOCK_WINDOW = timedelta(days=30)
TLS_STORE_NAME = "lan-tls"
TLS_STORE_RELATIVE = Path("security") / TLS_STORE_NAME
CA_BUNDLE_NAME = "ca.p12"
CA_CERTIFICATE_NAME = "root-ca.crt"
CA_ANCHOR_NAME = "ca.sha256"


class LanTLSSetupError(RuntimeError):
    """Raised when secure LAN TLS material is absent or cannot be trusted."""


@dataclass(frozen=True)
class ServerTLSMaterial:
    store_path: Path
    ca_bundle_path: Path
    root_certificate_path: Path
    certificate_path: Path
    private_key_path: Path
    root_fingerprint_sha256: str
    certificate_expires_at: datetime
    leaf_created: bool

    def launcher_json(self) -> dict[str, str | bool]:
        """Return only startup metadata; never include key or certificate bytes."""
        return {
            "store_path": str(self.store_path),
            "ca_bundle_path": str(self.ca_bundle_path),
            "root_certificate_path": str(self.root_certificate_path),
            "certificate_path": str(self.certificate_path),
            "private_key_path": str(self.private_key_path),
            "root_fingerprint_sha256": self.root_fingerprint_sha256,
            "certificate_expires_at": self.certificate_expires_at.isoformat(),
            "leaf_created": self.leaf_created,
        }


def default_tls_store_path(data_root: Path | None = None) -> Path:
    if data_root is None:
        from mrp.storage.paths import default_data_root

        data_root = default_data_root()
    return Path(data_root).expanduser() / TLS_STORE_RELATIVE


def _resolved_store(store_path: Path | str | None, code_root: Path | None) -> Path:
    if store_path is None:
        store_path = default_tls_store_path()
    resolved = Path(store_path).expanduser().resolve()
    if code_root is None:
        code_root = Path(__file__).resolve().parents[3]
    resolved_code = Path(code_root).expanduser().resolve()
    try:
        resolved.relative_to(resolved_code)
    except ValueError:
        return resolved
    raise LanTLSSetupError(
        "LAN TLS files must be stored outside the code directory. Set MRP_DATA_ROOT to a private external data folder."
    )


def _windows_identity() -> str:
    try:
        output = subprocess.check_output(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL,
        ).decode("ascii", errors="replace")
        fields = next(__import__("csv").reader(output.splitlines()), [])
        sid = fields[-1].strip() if fields else ""
        if re.fullmatch(r"S-1-(?:[0-9]+-){1,14}[0-9]+", sid):
            return sid
    except (OSError, subprocess.SubprocessError, StopIteration):
        pass
    raise LanTLSSetupError("Could not determine the current Windows account for the private LAN TLS folder.")


def _set_private_permissions(path: Path, *, directory: bool) -> None:
    """Restrict a TLS path to the current user and SYSTEM, or owner-only on POSIX."""
    try:
        if os.name == "nt":
            identity = _windows_identity()
            _set_windows_dacl(path, identity, directory=directory)
        else:
            os.chmod(path, 0o700 if directory else 0o600)
    except OSError as exc:
        raise LanTLSSetupError(
            "Could not restrict LAN TLS file permissions; secure LAN startup was refused."
        ) from exc


def _set_windows_dacl(path: Path, user_sid: str, *, directory: bool) -> None:
    """Set and verify a DACL only; this does not read/write owner, group, or SACL."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security_descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    descriptor_size = wintypes.DWORD()
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;{user_sid})(A;{inheritance};FA;;;S-1-5-18)"

    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    get_dacl = advapi.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_named = advapi.SetNamedSecurityInfoW
    set_named.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_named.restype = wintypes.DWORD
    get_named = advapi.GetNamedSecurityInfoW
    get_named.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get_named.restype = wintypes.DWORD
    free_local = kernel.LocalFree
    free_local.argtypes = [ctypes.c_void_p]
    free_local.restype = ctypes.c_void_p

    if not convert(sddl, 1, ctypes.byref(security_descriptor), ctypes.byref(descriptor_size)):
        code = ctypes.get_last_error()
        raise OSError(code, "Could not build the private Windows DACL.")
    actual_descriptor = ctypes.c_void_p()
    try:
        if not get_dacl(security_descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value:
            code = ctypes.get_last_error()
            raise OSError(code, "Could not read the private Windows DACL template.")
        # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        result = set_named(str(path), 1, 0x00000004 | 0x80000000, None, None, dacl, None)
        if result != 0:
            raise OSError(int(result), "Could not set the private Windows DACL.")

        # Read the DACL back and compare each ACE by SID, type, mask, and flags.
        # This catches explicit Everyone/Users ACEs as well as inherited grants.
        result = get_named(str(path), 1, 0x00000004, None, None, ctypes.byref(dacl), None, ctypes.byref(actual_descriptor))
        if result != 0:
            raise OSError(int(result), "Could not verify the private Windows DACL.")
        class AclSizeInformation(ctypes.Structure):
            _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD), ("AclBytesFree", wintypes.DWORD)]

        class AceHeader(ctypes.Structure):
            _fields_ = [("AceType", wintypes.BYTE), ("AceFlags", wintypes.BYTE), ("AceSize", wintypes.WORD)]

        acl_info = AclSizeInformation()
        get_acl_information = advapi.GetAclInformation
        get_acl_information.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        get_acl_information.restype = wintypes.BOOL
        get_ace = advapi.GetAce
        get_ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        get_ace.restype = wintypes.BOOL
        sid_to_string = advapi.ConvertSidToStringSidW
        sid_to_string.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        sid_to_string.restype = wintypes.BOOL
        if not get_acl_information(dacl, ctypes.byref(acl_info), ctypes.sizeof(acl_info), 2):
            code = ctypes.get_last_error()
            raise OSError(code, "Could not inspect the private Windows DACL.")
        observed: list[tuple[str, int, int, int]] = []
        for index in range(int(acl_info.AceCount)):
            ace = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace)):
                code = ctypes.get_last_error()
                raise OSError(code, "Could not inspect a private Windows access rule.")
            header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
            access_mask = ctypes.cast(int(ace.value) + ctypes.sizeof(AceHeader), ctypes.POINTER(wintypes.DWORD)).contents.value
            sid_pointer = ctypes.c_void_p(int(ace.value) + ctypes.sizeof(AceHeader) + ctypes.sizeof(wintypes.DWORD))
            sid_text = ctypes.c_void_p()
            if header.AceType != 0 or not sid_to_string(sid_pointer, ctypes.byref(sid_text)):
                raise OSError("The private Windows DACL contains a non-allow rule.")
            try:
                observed.append((ctypes.wstring_at(sid_text.value), int(access_mask), int(header.AceFlags), int(header.AceType)))
            finally:
                free_local(sid_text)
        expected_sids = sorted((user_sid, "S-1-5-18"))
        observed_sids = sorted(row[0] for row in observed)
        expected_flags = 0x03 if directory else 0
        if (
            observed_sids != expected_sids
            or len(observed) != 2
            or any(mask != 0x001F01FF or flags != expected_flags or ace_type != 0 for _, mask, flags, ace_type in observed)
        ):
            raise OSError("The Windows DACL contains an unexpected access rule.")
    finally:
        if actual_descriptor.value:
            free_local(actual_descriptor)
        if security_descriptor.value:
            free_local(security_descriptor)


def secure_private_file(path: Path | str) -> None:
    """Restrict one existing sensitive file without changing its parent ACL."""
    _set_private_permissions(Path(path), directory=False)


def _ensure_private_store(store_path: Path) -> None:
    try:
        store_path.mkdir(parents=True, exist_ok=True)
        if not store_path.is_dir():
            raise OSError("TLS storage path is not a directory")
        _set_private_permissions(store_path, directory=True)
    except OSError as exc:
        raise LanTLSSetupError("Could not create the private LAN TLS folder.") from exc


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_path: str | None = None
    try:
        fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        temporary = Path(temporary_path)
        _set_private_permissions(temporary, directory=False)
        with temporary.open("wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        temporary_path = None
        _set_private_permissions(path, directory=False)
    except OSError as exc:
        raise LanTLSSetupError("Could not safely write LAN TLS material.") from exc
    finally:
        if temporary_path:
            with contextlib.suppress(OSError):
                os.unlink(temporary_path)


@contextlib.contextmanager
def _exclusive_store_lock(store_path: Path):
    lock_path = store_path / ".lock"
    try:
        handle = lock_path.open("a+b")
        _set_private_permissions(lock_path, directory=False)
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        # Initialize only after locking. A concurrent opener can otherwise lock
        # the byte while another thread still has its first write buffered.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
    except OSError as exc:
        with contextlib.suppress(UnboundLocalError, OSError):
            handle.close()
        raise LanTLSSetupError("Could not lock the private LAN TLS folder.") from exc
    try:
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("TLS clock must be timezone-aware")
    return current.astimezone(UTC)


def _cert_times(certificate: x509.Certificate) -> tuple[datetime, datetime]:
    return certificate.not_valid_before_utc, certificate.not_valid_after_utc


def _public_bytes(public_key) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex().upper()


def _validate_root_certificate(
    certificate: x509.Certificate,
    now: datetime,
    private_key=None,
) -> None:
    if certificate.subject != certificate.issuer:
        raise ValueError("root is not self-issued")
    if private_key is not None and _public_bytes(private_key.public_key()) != _public_bytes(certificate.public_key()):
        raise ValueError("root private key does not match")
    if not isinstance(certificate.public_key(), ec.EllipticCurvePublicKey):
        raise ValueError("unsupported root key type")
    if not isinstance(certificate.public_key().curve, ec.SECP256R1):
        raise ValueError("unsupported root curve")
    constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    if not constraints.critical or not constraints.value.ca or constraints.value.path_length != 0:
        raise ValueError("root CA constraints are invalid")
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    if not usage.key_cert_sign or not usage.crl_sign:
        raise ValueError("root CA signing usage is invalid")
    before, after = _cert_times(certificate)
    if now < before or now >= after:
        raise ValueError("root CA is not currently valid")
    if after - now <= CA_RENEW_BLOCK_WINDOW:
        raise ValueError("root CA is near expiry")
    certificate.verify_directly_issued_by(certificate)


def _build_root(now: datetime) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sekai o Tsumugu Hime LAN Root CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CA_LIFETIME_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _load_root(store_path: Path, now: datetime) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    bundle_path = store_path / CA_BUNDLE_NAME
    certificate_path = store_path / CA_CERTIFICATE_NAME
    anchor_path = store_path / CA_ANCHOR_NAME
    if bundle_path.exists():
        try:
            _set_private_permissions(bundle_path, directory=False)
            if certificate_path.exists():
                _set_private_permissions(certificate_path, directory=False)
            if anchor_path.exists():
                _set_private_permissions(anchor_path, directory=False)
            raw = bundle_path.read_bytes()
            key, certificate, _ = pkcs12.load_key_and_certificates(raw, None)
            if key is None or certificate is None:
                raise ValueError("root key or certificate is missing")
            _validate_root_certificate(certificate, now, key)
            fingerprint = _fingerprint(certificate)
            if anchor_path.exists():
                anchor = anchor_path.read_text(encoding="ascii").strip().upper()
                if not re.fullmatch(r"[0-9A-F]{64}", anchor) or anchor != fingerprint:
                    raise ValueError("root fingerprint anchor does not match")
            if certificate_path.exists():
                published = x509.load_pem_x509_certificate(certificate_path.read_bytes())
                if _fingerprint(published) != fingerprint:
                    raise ValueError("stored public root does not match the signing CA")
            else:
                _atomic_write(
                    certificate_path,
                    certificate.public_bytes(serialization.Encoding.PEM),
                )
            if not anchor_path.exists():
                _atomic_write(anchor_path, (fingerprint + "\n").encode("ascii"))
            return key, certificate
        except LanTLSSetupError:
            raise
        except Exception as exc:
            raise LanTLSSetupError(
                "The existing LAN root CA is damaged, expired, or near expiry. It was not replaced. Restore its private backup or explicitly reset phone trust before creating a new CA."
            ) from exc

    # A missing private root key must never cause a new CA if any public trust
    # marker or issued leaf is left behind.
    has_history = anchor_path.exists() or certificate_path.exists() or any(store_path.glob("leaf-*.crt"))
    if has_history:
        raise LanTLSSetupError(
            "The LAN root CA private bundle is missing while prior trust files remain. It was not replaced; restore the original CA backup."
        )
    key, certificate = _build_root(now)
    try:
        bundle = pkcs12.serialize_key_and_certificates(
            name=b"mrp-lan-root-ca",
            key=key,
            cert=certificate,
            cas=None,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _atomic_write(bundle_path, bundle)
        _atomic_write(certificate_path, certificate.public_bytes(serialization.Encoding.PEM))
        _atomic_write(anchor_path, (_fingerprint(certificate) + "\n").encode("ascii"))
    except Exception:
        # Once the private key bundle reached disk it is preserved. A retry
        # will validate and finish publishing metadata around that same CA.
        raise
    return key, certificate


def _build_leaf(
    root_key: ec.EllipticCurvePrivateKey,
    root_certificate: x509.Certificate,
    address: ipaddress.IPv4Address,
    now: datetime,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(address))])
    root_expiry = _cert_times(root_certificate)[1]
    expires = min(now + timedelta(days=LEAF_LIFETIME_DAYS), root_expiry - timedelta(hours=1))
    if expires - now <= LEAF_RENEW_BEFORE:
        raise LanTLSSetupError(
            "The LAN root CA is too close to expiry to issue a useful site certificate. Restore or intentionally replace the CA and reinstall it on phones."
        )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(expires)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    return key, certificate


def _leaf_valid_for(
    certificate: x509.Certificate,
    key,
    root_certificate: x509.Certificate,
    address: ipaddress.IPv4Address,
    now: datetime,
) -> bool:
    try:
        if certificate.issuer != root_certificate.subject:
            return False
        if _public_bytes(certificate.public_key()) != _public_bytes(key.public_key()):
            return False
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        if constraints.value.ca:
            return False
        purposes = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.SERVER_AUTH not in purposes:
            return False
        alternatives = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if address not in alternatives.get_values_for_type(x509.IPAddress):
            return False
        before, after = _cert_times(certificate)
        if now < before or after - now <= LEAF_RENEW_BEFORE:
            return False
        certificate.verify_directly_issued_by(root_certificate)
        return True
    except (ValueError, x509.ExtensionNotFound, InvalidSignature):
        return False


def _existing_leaf(
    store_path: Path,
    root_certificate: x509.Certificate,
    address: ipaddress.IPv4Address,
    now: datetime,
) -> tuple[Path, Path, x509.Certificate] | None:
    safe_ip = str(address).replace(".", "-")
    paths = sorted(
        store_path.glob(f"leaf-{safe_ip}-*.crt"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for certificate_path in paths:
        key_path = certificate_path.with_suffix(".key")
        try:
            certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if _leaf_valid_for(certificate, key, root_certificate, address, now):
                _set_private_permissions(certificate_path, directory=False)
                _set_private_permissions(key_path, directory=False)
                return certificate_path, key_path, certificate
        except (OSError, ValueError, TypeError):
            # A partial leaf can result from power loss. It is not active and
            # is never used; a new complete, versioned pair is issued below.
            continue
    return None


def prepare_server_tls(
    selected_ip: str,
    *,
    data_root: Path | None = None,
    store_path: Path | str | None = None,
    code_root: Path | None = None,
    now: datetime | None = None,
) -> ServerTLSMaterial:
    """Create or validate a persistent private root and the selected-IP leaf."""
    try:
        address = ipaddress.IPv4Address(selected_ip)
    except ipaddress.AddressValueError as exc:
        raise LanTLSSetupError("LAN HTTPS requires a selected IPv4 address.") from exc
    current = _utc_now(now)
    resolved = _resolved_store(store_path or default_tls_store_path(data_root), code_root)
    _ensure_private_store(resolved)
    with _exclusive_store_lock(resolved):
        root_key, root_certificate = _load_root(resolved, current)
        leaf = _existing_leaf(resolved, root_certificate, address, current)
        leaf_created = leaf is None
        if leaf is None:
            leaf_key, leaf_certificate = _build_leaf(root_key, root_certificate, address, current)
            identifier = uuid.uuid4().hex
            prefix = f"leaf-{str(address).replace('.', '-')}-{identifier}"
            certificate_path = resolved / f"{prefix}.crt"
            private_key_path = resolved / f"{prefix}.key"
            _atomic_write(certificate_path, leaf_certificate.public_bytes(serialization.Encoding.PEM))
            _atomic_write(
                private_key_path,
                leaf_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            )
        else:
            certificate_path, private_key_path, leaf_certificate = leaf
        return ServerTLSMaterial(
            store_path=resolved,
            ca_bundle_path=resolved / CA_BUNDLE_NAME,
            root_certificate_path=resolved / CA_CERTIFICATE_NAME,
            certificate_path=certificate_path,
            private_key_path=private_key_path,
            root_fingerprint_sha256=_fingerprint(root_certificate),
            certificate_expires_at=_cert_times(leaf_certificate)[1],
            leaf_created=leaf_created,
        )


def _contained_file(path: Path | str, store_path: Path, code_root: Path) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    try:
        resolved.relative_to(code_root)
    except ValueError:
        pass
    else:
        raise LanTLSSetupError("LAN TLS material must be outside the code directory.")
    try:
        resolved.relative_to(store_path)
    except ValueError as exc:
        raise LanTLSSetupError("LAN TLS files must remain inside the private TLS folder.") from exc
    if not resolved.is_file():
        raise LanTLSSetupError("LAN TLS material file is missing.")
    _set_private_permissions(resolved, directory=False)
    return resolved


def validate_server_material(
    selected_ip: str,
    *,
    store_path: Path | str,
    certificate_path: Path | str,
    private_key_path: Path | str,
    root_certificate_path: Path | str,
    code_root: Path | None = None,
    now: datetime | None = None,
) -> ServerTLSMaterial:
    """Revalidate the chosen listener's certificate/key/root before binding."""
    try:
        address = ipaddress.IPv4Address(selected_ip)
    except ipaddress.AddressValueError as exc:
        raise LanTLSSetupError("LAN HTTPS requires a selected IPv4 address.") from exc
    resolved_store = _resolved_store(store_path, code_root)
    _ensure_private_store(resolved_store)
    resolved_code = Path(code_root or Path(__file__).resolve().parents[3]).resolve()
    try:
        cert_path = _contained_file(certificate_path, resolved_store, resolved_code)
        key_path = _contained_file(private_key_path, resolved_store, resolved_code)
        root_path = _contained_file(root_certificate_path, resolved_store, resolved_code)
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        root_certificate = x509.load_pem_x509_certificate(root_path.read_bytes())
        current = _utc_now(now)
        _validate_root_certificate(root_certificate, current)
        if not _leaf_valid_for(certificate, private_key, root_certificate, address, current):
            raise ValueError("server certificate does not match selected address, key, issuer, or validity")
        anchor_path = resolved_store / CA_ANCHOR_NAME
        if not anchor_path.is_file() or anchor_path.read_text(encoding="ascii").strip().upper() != _fingerprint(root_certificate):
            raise ValueError("root fingerprint anchor is invalid")
    except LanTLSSetupError:
        raise
    except Exception as exc:
        raise LanTLSSetupError(
            "The selected LAN certificate could not be verified against the private root CA; secure LAN startup was refused."
        ) from exc
    return ServerTLSMaterial(
        store_path=resolved_store,
        ca_bundle_path=resolved_store / CA_BUNDLE_NAME,
        root_certificate_path=root_path,
        certificate_path=cert_path,
        private_key_path=key_path,
        root_fingerprint_sha256=_fingerprint(root_certificate),
        certificate_expires_at=_cert_times(certificate)[1],
        leaf_created=False,
    )


def root_certificate_pem(material: ServerTLSMaterial) -> bytes:
    """Read only the already-validated public root certificate."""
    try:
        certificate = x509.load_pem_x509_certificate(material.root_certificate_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise LanTLSSetupError("The public LAN root certificate is unavailable.") from exc
    if _fingerprint(certificate) != material.root_fingerprint_sha256:
        raise LanTLSSetupError("The public LAN root certificate changed after startup.")
    return certificate.public_bytes(serialization.Encoding.PEM)


def verify_https_endpoint(
    address: str,
    port: int,
    root_certificate_path: Path | str,
    *,
    timeout: float = 2.0,
) -> dict:
    """Perform a real verified TLS connection and read the LAN status endpoint."""
    ip = str(ipaddress.IPv4Address(address))
    context = ssl.create_default_context(cafile=str(root_certificate_path))
    connection = http.client.HTTPSConnection(ip, port, context=context, timeout=timeout)
    try:
        connection.request("GET", "/api/v1/lan/status", headers={"Accept": "application/json"})
        response = connection.getresponse()
        payload = response.read(256 * 1024)
        if response.status != 200:
            raise LanTLSSetupError("Verified HTTPS health check returned an unexpected status.")
        status = json.loads(payload.decode("utf-8"))
        expected_url = f"https://{ip}:{port}/"
        if (
            not isinstance(status, dict)
            or status.get("enabled") is not True
            or status.get("https_available") is not True
            or status.get("transport") != "https"
            or status.get("bind_address") != ip
            or not isinstance(status.get("interfaces"), list)
            or len(status["interfaces"]) != 1
            or not isinstance(status["interfaces"][0], dict)
            or status["interfaces"][0].get("url") != expected_url
        ):
            raise LanTLSSetupError("Verified HTTPS health status does not match the selected LAN address.")
        return status
    except (OSError, ssl.SSLError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, LanTLSSetupError):
            raise
        raise LanTLSSetupError("The LAN HTTPS certificate or status check failed; the launcher will not use plaintext.") from exc
    finally:
        connection.close()


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and verify the private LAN HTTPS certificate.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="create or reuse CA and server certificate")
    prepare.add_argument("--ip", required=True)
    verify = subparsers.add_parser("verify", help="verify a real TLS connection and LAN status")
    verify.add_argument("--ip", required=True)
    verify.add_argument("--port", type=int, required=True)
    verify.add_argument("--ca-file", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            material = prepare_server_tls(args.ip)
            print(json.dumps(material.launcher_json(), ensure_ascii=False))
        else:
            status = verify_https_endpoint(args.ip, args.port, args.ca_file)
            print(json.dumps(status, ensure_ascii=False))
    except LanTLSSetupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
