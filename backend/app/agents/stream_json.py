"""Bounded incremental decoding of one exact, top-level JSON string.

This is only a decoder, never a visibility or narration validator. Callers must
validate complete candidate sentences against fixed public facts before release.
"""

import json


class IncrementalJSONObjectString:
    def __init__(self, field: str, max_chars: int = 262144):
        self.field = field
        self.max_chars = max_chars
        self.value = ""
        self.closed = False
        self.complete = False
        self.invalid = False
        self._raw = ""
        self._position = -1
        self._state = "start"
        self._key = ""
        self._keys = set()
        self._role = None
        self._string = ""
        self._escape = ""
        self._high_surrogate = None
        self._nested = []
        self._value_start = 0

    def feed(self, text: str) -> str:
        """Return decoded additions, withholding a chunk if it proves malformed."""
        if self.invalid:
            return ""
        if not isinstance(text, str) or len(self._raw) + len(text) > self.max_chars:
            self.invalid = True
            return ""
        self._raw += text
        previous = len(self.value)
        try:
            for char in text:
                self._position += 1
                self._consume(char)
        except (ValueError, TypeError, UnicodeError):
            self.invalid = True
            return ""
        return self.value[previous:]

    def finish(self) -> bool:
        """Require the complete object and exactly one string with the selected key."""
        if not self.complete or not self.closed:
            self.invalid = True
        return not self.invalid

    def _decoded(self, char):
        point = ord(char)
        if self._high_surrogate is not None:
            if not 0xDC00 <= point <= 0xDFFF:
                raise ValueError("unpaired surrogate")
            char = chr(0x10000 + ((self._high_surrogate - 0xD800) << 10) + point - 0xDC00)
            self._high_surrogate = None
        elif 0xD800 <= point <= 0xDBFF:
            self._high_surrogate = point
            return
        elif 0xDC00 <= point <= 0xDFFF:
            raise ValueError("unpaired surrogate")
        if self._role == "key":
            self._string += char
        elif self._role == "target":
            self.value += char

    def _consume_string(self, char):
        if self._escape:
            if self._escape == "\\":
                if char == "u":
                    self._escape += char
                    return
                decoded = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                           "n": "\n", "r": "\r", "t": "\t"}
                if char not in decoded:
                    raise ValueError("invalid escape")
                self._decoded(decoded[char])
                self._escape = ""
                return
            if char not in "0123456789abcdefABCDEF":
                raise ValueError("invalid unicode escape")
            self._escape += char
            if len(self._escape) == 6:
                self._decoded(chr(int(self._escape[2:], 16)))
                self._escape = ""
            return
        if char == "\\":
            self._escape = "\\"
        elif char == '"':
            if self._high_surrogate is not None:
                raise ValueError("unpaired surrogate")
            role, self._role = self._role, None
            if role == "key":
                self._key, self._string = self._string, ""
                if self._key in self._keys:
                    raise ValueError("duplicate key")
                self._keys.add(self._key)
                self._state = "colon"
            elif role == "target":
                self.closed = True
                self._state = "comma_or_end"
            elif not self._nested:
                self._state = "comma_or_end"
        else:
            if ord(char) < 32:
                raise ValueError("unescaped control character")
            self._decoded(char)

    def _begin_string(self, role):
        self._role = role
        self._string = self._escape = ""
        self._high_surrogate = None

    def _consume(self, char):
        if self._role:
            self._consume_string(char)
            return
        if self._state == "nested":
            if char == '"':
                self._begin_string("skip")
            elif char in "[{":
                self._nested.append(char)
            elif char in "]}":
                expected = "[" if char == "]" else "{"
                if not self._nested or self._nested.pop() != expected:
                    raise ValueError("invalid container")
                if not self._nested:
                    json.loads(self._raw[self._value_start:self._position + 1])
                    self._state = "comma_or_end"
            return
        if self._state == "primitive":
            if char not in ",}":
                return
            json.loads(self._raw[self._value_start:self._position])
            self._state = "comma_or_end"
        if char in " \r\n\t":
            return
        if self._state == "start":
            if char != "{":
                raise ValueError("root is not an object")
            self._state = "key_or_end"
        elif self._state in {"key_or_end", "key"}:
            if char == '"':
                self._begin_string("key")
            elif char == "}" and self._state == "key_or_end":
                self._end_object()
            else:
                raise ValueError("invalid object key")
        elif self._state == "colon":
            if char != ":":
                raise ValueError("missing colon")
            self._state = "value"
        elif self._state == "value":
            self._value_start = self._position
            if self._key == self.field:
                if char != '"':
                    raise ValueError("selected value is not a string")
                self._begin_string("target")
            elif char == '"':
                self._begin_string("skip")
            elif char in "[{":
                self._nested = [char]
                self._state = "nested"
            else:
                if char in ",}":
                    raise ValueError("missing value")
                self._state = "primitive"
        elif self._state == "comma_or_end":
            if char == ",":
                self._state = "key"
            elif char == "}":
                self._end_object()
            else:
                raise ValueError("invalid object separator")
        else:
            raise ValueError("content after JSON object")

    def _end_object(self):
        decoded = json.loads(self._raw[:self._position + 1])
        if not isinstance(decoded.get(self.field), str) or decoded[self.field] != self.value:
            raise ValueError("selected field missing or inconsistent")
        self._state = "done"
        self.complete = True
