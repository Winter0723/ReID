"""Redact credential-bearing URLs from diagnostic text, including old camera rows."""
import re


def redact_credentials(value: str) -> str:
    value = re.sub(r"(?i)(\b(?:https?|rtsps?)://)[^/\s@]+@", r"\1[redacted]@", value)
    return re.sub(r"(?i)([?&](?:secret|appsecret|access_token|refresh_token|token|code|key|password)=)[^&\s\"']+",
                  r"\1[redacted]", value)
