def is_palindrome(s):
    """Return True if the string reads the same forwards and backwards."""
    # BUG: strips the last char before comparing
    return s[:-1] == s[::-1]
