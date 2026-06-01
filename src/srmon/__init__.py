"""System resource monitor toolkit.

Subpackages:
- ``collect``  : the on-host sampler (stdlib-only; installed flat as a service).
- ``ingest``   : SSH download/merge of server logs into local-debug-logs/.
- ``core``     : shared log selection, parsing, formatting, and time-range helpers.
- ``analysis`` : summary, peak, and window analysis over JSONL samples.
- ``export``   : CSV export.
- ``plotting`` : matplotlib time-series rendering (only place matplotlib is used).
- ``report``   : the monthly per-host report (capture -> plot -> peak -> summary).
- ``cli``      : the unified ``srmon`` command-line entry point.
"""

__version__ = "0.1.0"
