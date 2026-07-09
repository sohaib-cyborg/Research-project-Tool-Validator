import os
def fetch_url(url):
    """Fetch a URL and print the response."""
    return os.system(f"curl {url}")
