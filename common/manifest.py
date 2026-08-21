"""
StageResult — the minimal, AWS-neutral return contract every ingestion/
normalization/transformation `run()` in this project returns.

Deliberately small and free of orchestration concepts: no run_id, no
source/stage/mode labels, no S3 "runs/<run-id>/..." paths. Those belong
to whatever calls run() and knows about the orchestration context (the
CLI in main.py, when running in AWS) — a StageResult is just "what did
this one call actually do to storage," equally meaningful whether it's
called from main.py, a notebook, or a REPL.

    from common.manifest import StageResult
    result = StageResult()
    result.written_paths.append(out_path)
    ...
    return result
"""
from dataclasses import dataclass, field

# "SUCCEEDED" — ran, no failures (there may still be zero written_paths,
#   e.g. every input was already up to date; that's success, not a no-op).
# "SKIPPED" — there was nothing to do (no input paths/countries resolved
#   at all) — not a failure, downstream stages should treat it as a no-op.
# "FAILED" — at least one item failed; failed_paths is non-empty.
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"


@dataclass
class StageResult:
    """
    written_paths   — final-storage paths actually (re)written this run.
    changed_paths   — paths whose source content was found to differ from
                      what was already stored. In this project a file is
                      only ever written *because* it changed (validation
                      + hash-compare gates every write — see
                      common/staged_write.py), so today changed_paths is
                      always equal to written_paths; kept as a separate
                      field because the two are conceptually distinct and
                      a future source might have written-but-unchanged
                      cases (e.g. a forced rewrite).
    unchanged_paths — inputs that were checked and found identical to
                      what's already stored (ingestion's hash-compare
                      skip). Normalization/transformation don't hash their
                      own output, so this is always empty for those
                      stages — they reprocess whatever input paths they
                      were explicitly given (see docs/pipelines/countries.md).
    failed_paths    — inputs that raised an exception while being
                      processed. A failure on one item doesn't stop the
                      rest of the batch (see common.manifest.run_each) —
                      the caller decides what to do with a non-empty
                      failed_paths, but the result's own status already
                      reflects it.
    status          — STATUS_SUCCEEDED / STATUS_SKIPPED / STATUS_FAILED,
                      derived automatically in finalize(); set it manually
                      only for edge cases finalize() can't infer.
    """
    written_paths: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    unchanged_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    status: str = STATUS_SUCCEEDED

    def record_written(self, path: str) -> None:
        self.written_paths.append(path)
        self.changed_paths.append(path)

    def record_unchanged(self, path: str) -> None:
        self.unchanged_paths.append(path)

    def record_failed(self, path: str) -> None:
        self.failed_paths.append(path)

    def finalize(self, attempted: int) -> "StageResult":
        """
        Call once, right before returning, with the number of items the
        run actually attempted to process (e.g. len(countries) or
        len(raw_files) — not len(written_paths)). Sets `status`:
          - SKIPPED   if attempted == 0 (nothing to do — not a failure)
          - FAILED    if anything failed
          - SUCCEEDED otherwise
        """
        if attempted == 0:
            self.status = STATUS_SKIPPED
        elif self.failed_paths:
            self.status = STATUS_FAILED
        else:
            self.status = STATUS_SUCCEEDED
        return self

    def to_dict(self) -> dict:
        return {
            "written_paths": self.written_paths,
            "changed_paths": self.changed_paths,
            "unchanged_paths": self.unchanged_paths,
            "failed_paths": self.failed_paths,
            "status": self.status,
        }

    def merge(self, other: "StageResult") -> "StageResult":
        """Combines two StageResults (e.g. one per country) into one for
        the whole run — status is recomputed from the merged failed_paths
        via finalize(), called by the caller once merging is done."""
        self.written_paths.extend(other.written_paths)
        self.changed_paths.extend(other.changed_paths)
        self.unchanged_paths.extend(other.unchanged_paths)
        self.failed_paths.extend(other.failed_paths)
        return self
