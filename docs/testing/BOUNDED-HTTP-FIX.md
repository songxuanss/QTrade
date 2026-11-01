# bounded supplier HTTP: subprocess start-method root cause

## Symptom

`tests/test_bounded_supplier_http.py` (8 tests) always failed when the suite ran
as `python -m pytest`:

- the local TCP test server never observed a connection (`accepted` stayed unset),
- the parent killed the socket owner after the deadline and reported `timeout`,
- or returned `unavailable` when the child exited early.

Standalone scripts using the same client against the same server worked.

## Root cause

The socket owner was started with `multiprocessing`'s `spawn` context. `spawn`
re-imports the caller's `__main__` module in the child. Under `python -m pytest`
that main module is pytest's bootstrap, so every socket owner spent its lifetime
starting a nested test runner instead of opening the socket. `forkserver` uses
the same `spawn.prepare()` path for the caller's main module and hung the same
way; a defensive watchdog did not help because the target function was never
reached.

## Fix

- Start the socket owner with the `fork` context on Linux. The child's direct
  parent is now the charged caller, so `PR_SET_PDEATHSIG` exactness is restored
  and the parent check (`os.getppid() == parent_pid`) is meaningful again.
- The worker-death test uses `fork` for the same reason (spawn/forkserver started
  a nested pytest in the child), keeping the assertion that killing the Worker
  closes the supplier socket.
- Trade-off: forking from a multi-threaded caller emits a CPython
  `DeprecationWarning`. The child only performs bounded HTTP I/O and private file
  writes and re-checks its parent, so no parent locks are used after the fork.

## Evidence

`pytest` (full suite) exits 0 with no failures (1357 passed, 298 skipped); see
`.tools/verification-local/review/full-pytest.txt`.
