"""Outlook .msg messages (an OLE container) -> e-mail, with attachments, and on into every e-mail format."""

import email
import email.policy
import sys
from pathlib import Path

import pymupdf
import pytest

from shapeshifter import engine, planner
from shapeshifter.core import Context
from shapeshifter.native import legacy

sys.path.insert(0, str(Path(__file__).parent))
from legacy_fixtures import build_msg                    # noqa: E402
from test_ocr import CFG                                  # noqa: E402


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


def msg(html=None):
    return build_msg("Quarterly numbers", "Hi Charles,\r\nThe numbers are attached.\r\n", html=html,
                     attachments=[("numbers.csv", b"a,b\n1,2\n"), ("note ünï.txt", b"hello")])


def parse(data: bytes):
    return email.message_from_bytes(data, policy=email.policy.default)


def test_headers_body_and_attachments():
    m = parse(legacy.msg_to_eml(msg()))
    assert m["Subject"] == "Quarterly numbers" and "Ada Lovelace" in m["From"] and "Charles Babbage" in m["To"]
    assert "2023" in m["Date"] and "Mar" in m["Date"]
    assert "The numbers are attached." in m.get_body(("plain",)).get_content()
    atts = {a.get_filename(): a.get_payload(decode=True) for a in m.iter_attachments()}
    assert set(atts) == {"numbers.csv", "note ünï.txt"}
    assert atts["numbers.csv"] == b"a,b\n1,2\n"


def test_html_bodies_are_kept():
    m = parse(legacy.msg_to_eml(msg(html="<p>Hello <b>there</b></p>")))
    assert "<b>there</b>" in m.get_body(("html",)).get_content()


def test_conversions_through_the_e_mail_formats(tmp_path):
    (tmp_path / "q.msg").write_bytes(msg(html="<p>Hello <b>there</b></p>"))
    eml = run(tmp_path / "q.msg", "eml")[0]
    assert b"Subject: Quarterly numbers" in eml.read_bytes()
    txt = run(tmp_path / "q.msg", "txt")[0].read_text(encoding="utf-8")
    assert "Subject: Quarterly numbers" in txt and "Attachments: numbers.csv" in txt
    with pymupdf.open(run(tmp_path / "q.msg", "pdf")[0]) as doc:
        assert "Quarterly numbers" in doc[0].get_text()
    assert planner.route("msg", "md") is not None


def test_not_a_message():
    with pytest.raises(legacy.LegacyError):
        legacy.msg_to_eml(b"x" * 2000)
