from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path

import pytest
from cryptography import x509

from mrp.server.lan_tls import (
    CA_LIFETIME_DAYS,
    LanTLSSetupError,
    prepare_server_tls,
    validate_server_material,
)


BASE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    code_root = tmp_path / "code"
    code_root.mkdir(parents=True)
    store_path = tmp_path / "private-data" / "security" / "lan-tls"
    return code_root, store_path


def _prepare(tmp_path: Path, address: str, now: datetime = BASE_TIME):
    code_root, store_path = _paths(tmp_path)
    return prepare_server_tls(
        address,
        store_path=store_path,
        code_root=code_root,
        now=now,
    )


def test_ca_is_reused_for_multiple_ips_and_leaf_has_only_selected_ip_san(tmp_path: Path) -> None:
    code_root, store_path = _paths(tmp_path)
    first = prepare_server_tls(
        "192.168.40.10", store_path=store_path, code_root=code_root, now=BASE_TIME
    )
    second = prepare_server_tls(
        "192.168.40.11", store_path=store_path, code_root=code_root, now=BASE_TIME + timedelta(days=1)
    )

    assert first.root_fingerprint_sha256 == second.root_fingerprint_sha256
    assert first.root_certificate_path.read_bytes() == second.root_certificate_path.read_bytes()
    assert first.certificate_path != second.certificate_path
    leaf = x509.load_pem_x509_certificate(second.certificate_path.read_bytes())
    alternatives = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert alternatives.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("192.168.40.11")]

    with pytest.raises(LanTLSSetupError):
        validate_server_material(
            "192.168.40.10",
            store_path=second.store_path,
            certificate_path=second.certificate_path,
            private_key_path=second.private_key_path,
            root_certificate_path=second.root_certificate_path,
            code_root=code_root,
            now=BASE_TIME + timedelta(days=1),
        )


def test_tls_store_inside_code_root_is_rejected(tmp_path: Path) -> None:
    code_root = tmp_path / "code"
    code_root.mkdir()
    with pytest.raises(LanTLSSetupError, match="outside the code directory"):
        prepare_server_tls(
            "192.168.40.10",
            store_path=code_root / "data" / "security" / "lan-tls",
            code_root=code_root,
            now=BASE_TIME,
        )


def test_leaf_renews_before_expiry_without_rotating_root_ca(tmp_path: Path) -> None:
    code_root, store_path = _paths(tmp_path)
    initial = prepare_server_tls(
        "192.168.40.10", store_path=store_path, code_root=code_root, now=BASE_TIME
    )
    initial_leaf_bytes = initial.certificate_path.read_bytes()
    initial_root_bytes = initial.root_certificate_path.read_bytes()

    renewed = prepare_server_tls(
        "192.168.40.10",
        store_path=store_path,
        code_root=code_root,
        now=BASE_TIME + timedelta(days=80),
    )

    assert renewed.leaf_created is True
    assert renewed.certificate_path != initial.certificate_path
    assert renewed.certificate_path.read_bytes() != initial_leaf_bytes
    assert renewed.root_fingerprint_sha256 == initial.root_fingerprint_sha256
    assert renewed.root_certificate_path.read_bytes() == initial_root_bytes


def test_damaged_or_expired_root_ca_is_not_silently_replaced(tmp_path: Path) -> None:
    code_root, damaged_store = _paths(tmp_path / "damaged")
    original = prepare_server_tls(
        "192.168.40.10", store_path=damaged_store, code_root=code_root, now=BASE_TIME
    )
    original_bundle = original.ca_bundle_path.read_bytes()
    original.ca_bundle_path.write_bytes(b"not a PKCS#12 bundle")
    with pytest.raises(LanTLSSetupError, match="damaged, expired, or near expiry"):
        prepare_server_tls(
            "192.168.40.10",
            store_path=damaged_store,
            code_root=code_root,
            now=BASE_TIME + timedelta(days=1),
        )
    assert original.ca_bundle_path.read_bytes() == b"not a PKCS#12 bundle"
    assert original_bundle != original.ca_bundle_path.read_bytes()

    expired_code, expired_store = _paths(tmp_path / "expired")
    expired = prepare_server_tls(
        "192.168.40.10", store_path=expired_store, code_root=expired_code, now=BASE_TIME
    )
    original_expired_bundle = expired.ca_bundle_path.read_bytes()
    with pytest.raises(LanTLSSetupError, match="damaged, expired, or near expiry"):
        prepare_server_tls(
            "192.168.40.10",
            store_path=expired_store,
            code_root=expired_code,
            now=BASE_TIME + timedelta(days=CA_LIFETIME_DAYS + 1),
        )
    assert expired.ca_bundle_path.read_bytes() == original_expired_bundle


def test_concurrent_preparation_reuses_one_ca_and_leaf(tmp_path: Path) -> None:
    code_root, store_path = _paths(tmp_path)

    def prepare(_index: int):
        return prepare_server_tls(
            "192.168.40.10",
            store_path=store_path,
            code_root=code_root,
            now=BASE_TIME,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        materials = list(executor.map(prepare, range(4)))

    assert len({item.root_fingerprint_sha256 for item in materials}) == 1
    assert len({item.certificate_path for item in materials}) == 1
    assert len({item.private_key_path for item in materials}) == 1
    assert sum(item.leaf_created for item in materials) == 1
