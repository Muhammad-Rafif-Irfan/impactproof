# Proof engine

The proof engine turns a named JSON scenario into bounded Python execution and an explicit expected-versus-actual comparison.

## Lifecycle

```text
Scenario → execution → capture → extraction → comparison → status
```

1. **Scenario:** `proof/scenarios/<name>.json` declares calls, arguments, capture names, extraction rules, and expected values.
2. **Execution:** the engine generates a small Python driver and runs it in a subprocess from the target repository context. The subprocess has a 30-second timeout.
3. **Capture:** each step's function return value is stored under its `capture` name.
4. **Extraction:** configured values are read directly from a capture or from nested dictionaries using a dot-separated key path.
5. **Comparison:** actual values are compared with the scenario's expected values. Numeric values use a small `1e-9` tolerance for floating-point representation.
6. **Status:** all compared values matching yields `PASS`; mismatches yield `REGRESSION`; missing data or execution/extraction failure yields `ERROR`.

The scenario specifies what is exercised. The engine does not infer test coverage or prove unexecuted behavior.

## Scenario format

The current `premium_checkout_refund` scenario has this shape (shortened to its relevant fields):

```json
{
  "scenario": "premium_checkout_refund",
  "steps": [
    {
      "id": "checkout_step",
      "module": "checkout",
      "function": "checkout",
      "args": [100],
      "kwargs": {"is_premium": true},
      "capture": "transaction"
    },
    {
      "id": "refund_step",
      "module": "refund",
      "function": "refund",
      "args_from_capture": ["transaction"],
      "capture": "refund_amount"
    }
  ],
  "expected": {
    "final_amount": 90.0,
    "invoice_final_amount": 90.0,
    "refund_amount": 90.0
  },
  "extract": {
    "final_amount": {"from_capture": "transaction", "key": "final_amount"},
    "invoice_final_amount": {"from_capture": "transaction", "key": "invoice.final_amount"},
    "refund_amount": {"from_capture": "refund_amount", "direct": true}
  }
}
```

`key: "final_amount"` reads `capture["final_amount"]`; `key: "invoice.final_amount"` traverses dictionaries as `capture["invoice"]["final_amount"]`. It is dictionary traversal, not Python expression evaluation.

## Running a scenario

From the repository root:

```bash
python3 proof/run_proof.py /path/to/target-repository premium_checkout_refund
```

The CLI emits a JSON result. Its exit code is `0` for `PASS` and `1` for `REGRESSION` or `ERROR`.

## Current demo result

In the current demo working tree, `final_amount` and `invoice_final_amount` are both `90`, while `refund_amount` is `100` against an expected `90`. The proof status is `REGRESSION`, with one mismatch: `refund_amount`.

