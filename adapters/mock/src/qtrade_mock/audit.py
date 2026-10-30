"""Audit double records the same safe facts without storing arbitrary payloads."""

from threading import Lock

from qtrade_domain.audit_port import AuditRecord


class MockAuditStore:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self._lock = Lock()

    def append(self, record: AuditRecord) -> int:
        with self._lock:
            self.records.append(record)
            return len(self.records)
