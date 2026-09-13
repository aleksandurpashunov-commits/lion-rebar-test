from __future__ import annotations


class ExcelExportError(RuntimeError):
    """Base error for production export and production-shape validation."""


class F006AsymmetricHookError(ExcelExportError):
    """Known F006 topology case with unequal start/end hook lengths.

    This type is a cross-layer contract, not a display-only message.  The
    recognition pipeline uses it to keep a proven F006 form STRONG while
    classifying the production dimensions separately as DERIVED/REVIEW.
    Therefore callers must never catch and replace it with a generic export
    error.
    """

    def __init__(self, hook_start: float, hook_end: float) -> None:
        self.hooks = (float(hook_start), float(hook_end))
        super().__init__(
            f"F006 има различни ключове {hook_start:g}/{hook_end:g} cm; "
            "MASTER изисква еднакви L1."
        )
