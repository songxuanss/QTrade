"""Traceable path from a normalized Bar through dataset, batch, task, and findings."""

from dataclasses import dataclass

from qtrade_contracts import Bar, DataBatch
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.ingestion import IngestionTask
from qtrade_domain.quality import QualityIssue


@dataclass(frozen=True)
class BarLineage:
    bar: Bar
    dataset: PublishedDataset
    batch: DataBatch
    collection: IngestionTask
    quality_issues: tuple[QualityIssue, ...]
