"""Input validation at the system boundary. Deny-by-default."""
import sqlite3  # noqa: F401  (imported to keep data-layer error mapping close)


MAX_TITLE = 200
MAX_NOTES = 5000
ALLOWED_FIELDS = ("title", "notes", "done")


class ValidationError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def validate_payload(data, partial=False):
    """Validate an untrusted JSON payload; return only whitelisted fields.

    Unknown fields are rejected (deny-by-default); types and lengths are
    checked for every supplied field; the required title is enforced on
    creates only after the field whitelist passes.
    """
    if not isinstance(data, dict):
        raise ValidationError("body", "request body must be a JSON object")

    clean = {}
    for key, value in data.items():
        if key not in ALLOWED_FIELDS:
            raise ValidationError(key, f"unknown field '{key}' is not accepted")
        if key == "title":
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("title", "'title' must be a non-empty string")
            if len(value) > MAX_TITLE:
                raise ValidationError("title", f"'title' must be at most {MAX_TITLE} characters")
            clean["title"] = value.strip()
        elif key == "notes":
            if value is None:
                clean["notes"] = ""
            elif isinstance(value, str):
                if len(value) > MAX_NOTES:
                    raise ValidationError("notes", f"'notes' must be at most {MAX_NOTES} characters")
                clean["notes"] = value
            else:
                raise ValidationError("notes", "'notes' must be a string or null")
        elif key == "done":
            if not isinstance(value, bool):
                raise ValidationError("done", "'done' must be a boolean")
            clean["done"] = value
    if not partial and "title" not in clean:
        raise ValidationError("title", "a non-empty 'title' string is required")
    return clean
