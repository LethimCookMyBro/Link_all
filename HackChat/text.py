def is_arabic(text):
    for ch in text:
        if "\u0600" <= ch <= "\u06FF":
            return True
    return False


def fix_arabic(text):
    if is_arabic(text):
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            reshaped = arabic_reshaper.reshape(text)
            return get_display(reshaped)
        except ImportError:
            pass
    return text


def has_bidi_support():
    try:
        import bidi.algorithm  # noqa: F401

        return True
    except ImportError:
        return False
