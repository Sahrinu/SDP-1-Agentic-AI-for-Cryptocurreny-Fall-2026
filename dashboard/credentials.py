"""Process-session credentials, deliberately separate from serializable app state."""
import os
import threading


def parse_key_payload(raw):
    import json
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('Enter a valid key in the Gemini field') from None
    if not isinstance(payload, dict) or set(payload) != {'api_key'}:
        raise ValueError('Expected one API key field')
    key = payload['api_key']
    if not isinstance(key, str):
        raise ValueError('The API key must be text')
    key = key.strip()
    if not key or len(key) > 4096 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError('Enter a nonempty API key without spaces')
    return key


class SessionCredentials:
    """Only public_status may be serialized. Clear falls back to environment."""
    def __init__(self):
        self._key = None
        self._lock = threading.Lock()

    def __repr__(self):
        return '<SessionCredentials: redacted>'

    def set_key(self, key):
        with self._lock:
            self._key = key

    def clear(self):
        with self._lock:
            self._key = None

    def resolved_key(self):
        with self._lock:
            return self._key or os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')

    def public_status(self):
        with self._lock:
            source = 'session' if self._key else 'environment' if (
                os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')) else None
            return {'configured':source is not None,'source':source,
                    'storage':'server memory only' if source == 'session' else source,
                    'validation':'Checked on the next explicit Gemini request'}
