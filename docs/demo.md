# Premium pricing demo

This walkthrough uses the demo application only to illustrate a general developer problem: a local code change can affect downstream behavior.

## Story

**Developer task:** “Introduce a premium pricing policy.”

The working-tree change applies a 10% discount to qualifying premium orders with a minimum spend of $50. The scenario uses a $100 premium purchase:

```text
Original amount: $100
Discount:        10%
Final amount:    $90
```

The static dependency chain demonstrated by the repository is:

```text
discount.py → checkout.py → payment.py → invoice.py → refund.py
```

The demo intentionally preserves a refund bug: `refund.py::refund` uses `original_amount` rather than the paid `final_amount`.

## Reproduce the deterministic result

From the ImpactProof repository root, with the demo repository at `/path/to/impactproof-demo`:

```bash
python3 proof/run_proof.py /path/to/impactproof-demo premium_checkout_refund
```

For the current checked-in demo state, the proof result is:

| Value | Expected | Actual | Result |
| --- | ---: | ---: | --- |
| `final_amount` | 90 | 90 | PASS |
| `invoice_final_amount` | 90 | 90 | PASS |
| `refund_amount` | 90 | 100 | REGRESSION |

Only `refund_amount` mismatches. The CLI exits with status `1` because the scenario reports a regression.

## Dashboard rehearsal

```bash
python3 graph/launch_graph.py /path/to/impactproof-demo --scenario premium_checkout_refund
```

The browser opens the dashboard in its idle state. The sequence is:

1. Click **INVESTIGATE CHANGE**.
2. Show the **Predicted Impact** graph as the static-analysis blast radius.
3. Show **Proven by Execution** and the three actual scenario values.
4. Select the regression evidence and request Bob's explanation.
5. Review the root cause and proposed targeted correction.
6. Request **Apply Fix**. Bob's bridge accepts only the intended `apply_fix` MCP invocation/result for the selected source target.
7. Click **Verify** separately. A fix is verified only when the proof returns `PASS`.
8. Optionally use **Undo Bob Fix**; undo is explicit and uses recorded targeted state.

The current demo repository is intentionally in the regression state. The walkthrough does not claim that it is currently fixed. The regression-tool tests also exercise verification with a corrected temporary fixture; that test fixture is not the demo repository.

## Bob IDE workflow

The repository also includes `.bob/commands/impactproof-investigate.md` for the MCP-oriented workflow. The dashboard launch command `/impact` is a local hackathon setup instruction and assumes its installed launcher and Bob paths have been adapted for the machine running the demo.

