"""UI document routes and opt-in HTML handling for missing pages."""

import re


def is_app_page(path):
    return path in ("/", "/browse", "/api-docs") or bool(re.fullmatch(
        r"/records/(?:hemi/0x[0-9a-f]{64}|publications/[0-9a-f]{64})", path))


def wants_html_not_found(path, accept):
    namespace = path.lstrip("/").split("/", 1)[0]
    if namespace in ("api", "health", "assets") or any(part.startswith(".") for part in path.split("/") if part):
        return False
    for item in accept.lower().split(","):
        media, *options = (part.strip() for part in item.split(";"))
        if media == "text/html":
            try:
                return float(next((option[2:] for option in options if option.startswith("q=")), "1")) > 0
            except ValueError:
                return False
    return False
