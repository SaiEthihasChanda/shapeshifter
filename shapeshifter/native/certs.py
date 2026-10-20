"""PEM / DER certificates and keys: a minimal ASN.1 reader and X.509 summary.

Conversions: PEM <-> DER, and certificate details -> text / JSON. Private keys are
detected so the engine can restrict them to PEM <-> DER (see identify._sensitivity).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timezone

from ..core import ConversionError
from .registry import register

PEM_RX = re.compile(rb"-----BEGIN ([A-Z0-9 ]+)-----\s*(.*?)\s*-----END \1-----", re.S)

OIDS = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST", "2.5.4.10": "O",
    "2.5.4.11": "OU", "2.5.4.5": "serialNumber", "1.2.840.113549.1.9.1": "emailAddress",
    "0.9.2342.19200300.100.1.25": "DC",
    "1.2.840.113549.1.1.1": "RSA", "1.2.840.10045.2.1": "EC", "1.3.101.112": "Ed25519",
    "1.3.101.113": "Ed448", "1.2.840.10040.4.1": "DSA",
    "1.2.840.113549.1.1.5": "sha1WithRSA", "1.2.840.113549.1.1.11": "sha256WithRSA",
    "1.2.840.113549.1.1.12": "sha384WithRSA", "1.2.840.113549.1.1.13": "sha512WithRSA",
    "1.2.840.113549.1.1.10": "RSASSA-PSS", "1.2.840.10045.4.3.2": "ecdsa-with-SHA256",
    "1.2.840.10045.4.3.3": "ecdsa-with-SHA384", "1.2.840.10045.4.3.4": "ecdsa-with-SHA512",
    "1.2.840.10045.3.1.7": "P-256", "1.3.132.0.34": "P-384", "1.3.132.0.35": "P-521",
    "2.5.29.17": "subjectAltName", "2.5.29.19": "basicConstraints", "2.5.29.15": "keyUsage",
    "2.5.29.37": "extKeyUsage", "1.2.840.113549.1.7.2": "pkcs7-signedData",
    "1.2.840.113549.1.5.13": "PBES2",
}


# ----------------------------------------------------------------------- ASN.1

class Node:
    __slots__ = ("tag", "cls", "constructed", "value", "children")

    def __init__(self, tag, cls, constructed, value, children):
        self.tag, self.cls, self.constructed, self.value, self.children = \
            tag, cls, constructed, value, children


def parse(data: bytes, pos: int = 0, end: int | None = None, depth: int = 0) -> list[Node]:
    end = len(data) if end is None else end
    nodes = []
    while pos < end:
        if depth > 40:
            raise ConversionError("ASN.1 nesting too deep")
        first = data[pos]
        cls, constructed, tag = first >> 6, bool(first & 0x20), first & 0x1F
        pos += 1
        if tag == 0x1F:
            tag = 0
            while data[pos] & 0x80:
                tag = (tag << 7) | (data[pos] & 0x7F)
                pos += 1
            tag = (tag << 7) | data[pos]
            pos += 1
        length = data[pos]
        pos += 1
        if length & 0x80:
            n = length & 0x7F
            if n == 0 or n > 4:
                raise ConversionError("Unsupported ASN.1 length")
            length = int.from_bytes(data[pos:pos + n], "big")
            pos += n
        if pos + length > end:
            raise ConversionError("Truncated ASN.1 data")
        value = data[pos:pos + length]
        children = parse(data, pos, pos + length, depth + 1) if constructed else []
        nodes.append(Node(tag, cls, constructed, value, children))
        pos += length
    return nodes


def oid(value: bytes) -> str:
    parts = [value[0] // 40, value[0] % 40]
    n = 0
    for b in value[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return ".".join(map(str, parts))


def _is(node: Node, tag: int, cls: int = 0) -> bool:
    return node.tag == tag and node.cls == cls


def der_kind(data: bytes) -> str:
    """certificate | csr | private key | public key | pkcs7 | '' (unknown)."""
    try:
        top = parse(data)[0]
    except Exception:
        return ""
    if not _is(top, 0x10) or not top.children:
        return ""
    c = top.children
    try:
        if len(c) == 3 and _is(c[0], 0x10) and _is(c[1], 0x10) and _is(c[2], 0x03):
            tbs = c[0].children
            if len(tbs) > 1 and _is(tbs[0], 0x02) and tbs[0].value == b"\x00" and \
                    _is(tbs[1], 0x10) and tbs[1].children and _is(tbs[1].children[0], 0x11):
                return "csr"
            return "certificate"
        if len(c) >= 3 and _is(c[0], 0x02) and c[0].value in (b"\x00", b"\x01") and \
                (_is(c[1], 0x10) or _is(c[1], 0x02) or _is(c[1], 0x04)):
            return "private key"          # PKCS#8, PKCS#1 RSA, SEC1 EC
        if len(c) == 2 and _is(c[0], 0x10) and _is(c[1], 0x04):
            return "private key"          # encrypted PKCS#8
        if len(c) == 2 and _is(c[0], 0x10) and _is(c[1], 0x03):
            return "public key"           # SubjectPublicKeyInfo
        if len(c) == 2 and _is(c[0], 0x02) and _is(c[1], 0x02):
            return "public key"           # PKCS#1 RSA public
        if _is(c[0], 0x06) and oid(c[0].value).startswith("1.2.840.113549.1.7"):
            return "pkcs7"
    except Exception:
        return ""
    return ""


PEM_LABELS = {"certificate": "CERTIFICATE", "csr": "CERTIFICATE REQUEST",
              "private key": "PRIVATE KEY", "public key": "PUBLIC KEY", "pkcs7": "PKCS7"}


# ------------------------------------------------------------------------- PEM

def pem_blocks(data: bytes) -> list[tuple[str, bytes]]:
    out = []
    for m in PEM_RX.finditer(data):
        body = m.group(2)
        if b"Proc-Type:" in body:            # legacy encrypted PEM: headers before data
            body = body.split(b"\n\n", 1)[-1] if b"\n\n" in body else body.split(b"\r\n\r\n", 1)[-1]
        try:
            out.append((m.group(1).decode(), base64.b64decode(b"".join(body.split()))))
        except binascii.Error as e:
            raise ConversionError(f"Damaged PEM block: {e}") from e
    if not out:
        raise ConversionError("No PEM blocks found")
    return out


def to_pem(der: bytes, label: str) -> bytes:
    b64 = base64.b64encode(der).decode()
    lines = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n".encode()


# ------------------------------------------------------------------- summaries

def _string(node: Node) -> str:
    if node.tag == 0x1E:                       # BMPString
        return node.value.decode("utf-16-be", "replace")
    return node.value.decode("utf-8", "replace")


def _name(node: Node) -> str:
    parts = []
    for rdn in node.children:
        for atv in rdn.children:
            if len(atv.children) == 2:
                parts.append(f"{OIDS.get(oid(atv.children[0].value), oid(atv.children[0].value))}"
                             f"={_string(atv.children[1])}")
    return ", ".join(parts)


def _time(node: Node) -> str:
    s = node.value.decode()
    fmt = "%y%m%d%H%M%SZ" if node.tag == 0x17 else "%Y%m%d%H%M%SZ"
    try:
        return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return s


def certificate_info(der: bytes) -> dict:
    top = parse(der)[0]
    tbs, alg, _sig = top.children
    f = tbs.children
    i = 0
    version = 1
    if f[0].cls == 2 and f[0].tag == 0:        # [0] EXPLICIT version
        version = int.from_bytes(f[0].children[0].value, "big") + 1
        i = 1
    serial = f[i].value.hex(":").upper()
    issuer = _name(f[i + 2])
    nb, na = (_time(t) for t in f[i + 3].children)
    subject = _name(f[i + 4])
    spki = f[i + 5]
    key_alg = OIDS.get(oid(spki.children[0].children[0].value), oid(spki.children[0].children[0].value))
    info = {"type": "X.509 certificate", "version": version, "serial": serial,
            "subject": subject, "issuer": issuer, "not_before": nb, "not_after": na,
            "key_algorithm": key_alg,
            "signature_algorithm": OIDS.get(oid(alg.children[0].value), oid(alg.children[0].value)),
            "self_signed": subject == issuer}
    if key_alg == "RSA":
        try:
            rsa = parse(spki.children[1].value[1:])[0]
            info["key_bits"] = (len(rsa.children[0].value.lstrip(b"\x00"))) * 8
        except Exception:
            pass
    for ext_block in f[i + 6:]:
        if ext_block.cls == 2 and ext_block.tag == 3:
            for ext in ext_block.children[0].children:
                if oid(ext.children[0].value) == "2.5.29.17":
                    names = parse(ext.children[-1].value)[0].children
                    info["subject_alt_names"] = [n.value.decode("ascii", "replace")
                                                 for n in names if n.cls == 2 and n.tag in (1, 2, 6)]
    return info


def summarize(der: bytes) -> dict:
    kind = der_kind(der)
    if kind == "certificate":
        return certificate_info(der)
    if kind == "csr":
        info = parse(der)[0].children[0].children
        return {"type": "Certificate signing request", "subject": _name(info[1]),
                "key_algorithm": OIDS.get(oid(info[2].children[0].children[0].value), "?")}
    if kind == "public key":
        return {"type": "Public key"}
    if kind == "private key":
        raise ConversionError("Refusing to write out a private key's details.")
    return {"type": kind or "unknown ASN.1 structure", "bytes": len(der)}


def _text(items: list[dict]) -> str:
    out = []
    for info in items:
        for k, v in info.items():
            out.append(f"{k.replace('_', ' ').capitalize():22} {', '.join(v) if isinstance(v, list) else v}")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ converters

@register("pem", "der")
def pem_to_der(data: bytes, src, dst, ctx) -> bytes:
    blocks = pem_blocks(data)
    return blocks[0][1]        # DER holds one object; the first (leaf) certificate


@register("der", "pem")
def der_to_pem(data: bytes, src, dst, ctx) -> bytes:
    return to_pem(data, PEM_LABELS.get(der_kind(data), "CERTIFICATE"))


@register(["pem", "der"], ["txt", "json"], terminal=True)
def cert_details(data: bytes, src, dst, ctx) -> bytes:
    ders = [d for _, d in pem_blocks(data)] if src == "pem" else [data]
    items = [summarize(d) for d in ders]
    if dst == "json":
        return json.dumps(items[0] if len(items) == 1 else items, indent=2).encode()
    return _text(items).encode()
