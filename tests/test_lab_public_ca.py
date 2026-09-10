"""Persist only parsed public certificate material from the lab secret value."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
import sys
from types import ModuleType
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def runtime():
    path=Path(__file__).resolve().parents[1]/'infra/aws/lab/runtime.py'
    spec=importlib.util.spec_from_file_location('lab_public_ca_test',path)
    module=importlib.util.module_from_spec(spec)
    # Only certificate handling is exercised; no AWS client is constructed.
    config=ModuleType('botocore.config')
    config.Config=object
    with patch.dict(sys.modules,{'boto3':ModuleType('boto3'),'botocore':ModuleType('botocore'),'botocore.config':config}):
        spec.loader.exec_module(module)
    return module


def test_public_certificate_serialization_excludes_accidental_private_material(tmp_path):
    module=runtime()
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'test-hub')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
                 .public_key(key.public_key()).serial_number(x509.random_serial_number())
                 .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                 .sign(key,hashes.SHA256()))
    public=certificate.public_bytes(serialization.Encoding.PEM)
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    target=tmp_path/'ca.pem'
    module.write_public_ca((public+private).decode(),target)
    assert target.read_bytes()==public
    assert b'PRIVATE KEY' not in target.read_bytes()
    for invalid in [private.decode(),'-----BEGIN CERTIFICATE-----\nnot-a-certificate','access-token']:
        with pytest.raises(ValueError):
            module.write_public_ca(invalid,target)
        assert target.read_bytes()==public
