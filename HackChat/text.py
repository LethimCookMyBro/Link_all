def is_arabic(text):
    for ch in text:
        if "\u0600" <= ch <= "\u06FF":
            return True
    return False


def fix_arabic(text):
    return text


def has_bidi_support():
    return False

