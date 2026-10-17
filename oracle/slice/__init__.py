"""Oracle P3 synthetic OpenCode slice (deterministic, offline).

Trust domain layout (see ``docs/oracle/P3_SLICE.md``):

* **Trusted host** - this package. Owns the visible *and* held-out corpus, the
  build/run harness, the completeness comparator, controller and receipts.
* **Agent workspace** - a candidate-only directory materialized by the
  controller. It never contains held-out expectations or Oracle source.
* **Controller** - ``oracle.slice.controller``. Sole owner of budget, attempt
  limits, candidate freezing and the terminal held-out verdict.

Isolation between the agent workspace and the held-out corpus is an
application-level boundary, **not** an OS sandbox; that protection is deferred
to a later phase.
"""
