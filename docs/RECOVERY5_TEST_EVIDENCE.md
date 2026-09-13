# Recovery.5 — test evidence

## Unit/regression suite

Command (Linux audit environment):

```text
PYTHONPATH=. pytest -o addopts='' -ra -k 'not test_batch_worker_no_longer_generates_the_auto_draft_excel'
```

Result:

```text
326 passed, 12 skipped, 1 deselected in 12.42s
```

The deselected test imports PySide6 and is environment-blocked in the Linux audit environment. Windows build/dist smoke remains a release gate.

`tests/test_matcher_evidence.py`: 24/24 PASS. New coverage locks:

- generic layer score threshold 0.10;
- diameter-coded layer score threshold 0.04;
- generic distance threshold 15 raw units;
- diameter-coded distance threshold 8 raw units;
- strict A–E candidate tiebreak;
- length mismatch remains blocking even with strong separation.

## Real DXF rerun

Input: `original.dxf`

- source SHA256: `66da0575ef0d4055f7fcd1e36519e7e0ca9a0de9a6ac47ad1df5f6ac645723ee`
- annotations: 315/315
- actionable positions: 150
- positions with candidate: 150
- geometry-strong: 116
- strong F006: 32
- Matcher CONTROL: 148
- Matcher MANUAL: 2
- AUTO gate: 148/150
- errors: 0
- MASTER ambiguities: 0
- ownership conflicts: 0

Remaining fail-closed positions:

- `3F12::BJ-FI-L` — `2N10x270`, F001, ΔL = 5.359 cm
- `4DC8::BJ-FI-L` — `2N12x270`, F001, ΔL = 6.883 cm

No length tolerance was widened.

## Windows gate

Still required before final release:

1. INSTALL;
2. BUILD_EXE;
3. packaged EXE starts with Engine 0.3.13;
4. Run 1 / Run 2 / restart / Run 3 all show 148 AUTO, 2 manual, 0 skipped manual approvals;
5. Final control reports exactly 2 unresolved;
6. export stays blocked until those two are reviewed or explicitly excluded.
