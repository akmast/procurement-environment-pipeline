"""
EEA measurements normalization — not yet implemented.

ingestion.eea.measurements currently saves parquet files exactly as
received from the API (no transformations happen there), so there is
nothing to move here yet. This module will eventually handle: combining
per-file parquet into a single dataset, deduplication, and any schema
normalization needed before joining with station metadata.
"""
