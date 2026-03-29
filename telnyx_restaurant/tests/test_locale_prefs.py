from telnyx_restaurant.locale_prefs import assistant_locale_hint, normalize_preferred_locale


def test_normalize_ko_variants() -> None:
    assert normalize_preferred_locale("ko") == "ko"
    assert normalize_preferred_locale("KO") == "ko"
    assert normalize_preferred_locale("ko-KR") == "ko"
    assert normalize_preferred_locale("korean") == "ko"


def test_normalize_default_en() -> None:
    assert normalize_preferred_locale(None) == "en"
    assert normalize_preferred_locale("") == "en"
    assert normalize_preferred_locale("fr") == "en"


def test_assistant_locale_hint() -> None:
    assert assistant_locale_hint("ko") == "ko-KR"
    assert assistant_locale_hint("en") == "en-US"
