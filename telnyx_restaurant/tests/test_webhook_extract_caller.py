"""Caller extraction for Telnyx dynamic variables (SIP opaque ``from`` vs real PSTN)."""

from __future__ import annotations

from telnyx_restaurant.webhook_payload import extract_caller_number


def test_prefers_telnyx_end_user_target_over_opaque_sip_from() -> None:
    cred = "gencred3g3omnmI1BywYcKi2MwGM8m4GABDyKaWUiuR1erO1X@sip.telnyx.com"
    got = extract_caller_number(
        {
            "from": cred,
            "telnyx_end_user_target": "+19259897818",
        }
    )
    assert got == "+19259897818"


def test_skips_opaque_at_sign_local_part_falls_back_to_next_candidate() -> None:
    cred = "opaqueid@sip.telnyx.com"
    got = extract_caller_number(
        {
            "from": cred,
            "caller_number": "+1 (925) 989-7818",
        }
    )
    assert got == "+1 (925) 989-7818"


def test_nested_payload_telnyx_before_from() -> None:
    cred = "whatever@sip.telnyx.com"
    got = extract_caller_number(
        {
            "data": {
                "payload": {
                    "from": cred,
                    "telnyx_end_user_target": "+19259897818",
                }
            }
        }
    )
    assert got == "+19259897818"


def test_sip_uri_with_e164_user_part_is_usable() -> None:
    got = extract_caller_number({"from": "sip:+19259897818@sip.telnyx.com"})
    assert got == "sip:+19259897818@sip.telnyx.com"


def test_only_opaque_returns_that_string_last_resort() -> None:
    cred = "onlyopaque@sip.telnyx.com"
    assert extract_caller_number({"from": cred}) == cred
