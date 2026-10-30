"""Explicit legacy seam for fixtures replacing fetch, not acquisition/normalization.

Attribute writes reach the wrapped HTTP/usage fixture too. Hiding only the new
prepared interface keeps these tests at their original queue/budget test surface.
New prepared Worker tests use the real unwrapped adapter separately.
"""


class LegacyCollectionFixture:
    def __init__(self, wrapped):
        object.__setattr__(self, "wrapped", wrapped)

    def __getattr__(self, name):
        if name in {"fetch_prepared", "normalize"}:
            raise AttributeError(name)
        return getattr(self.wrapped, name)

    def __setattr__(self, name, value):
        setattr(self.wrapped, name, value)
