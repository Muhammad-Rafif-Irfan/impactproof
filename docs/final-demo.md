# Final hackathon demo sequence

The demo repository should start in its intentional regression state. This walkthrough uses the dashboard as the visible product and IBM Bob as its reasoning/remediation layer.

## Step 1 — Open the dashboard

From the ImpactProof repository root, launch the existing viewer for a compatible demo repository:

```bash
python3 graph/launch_graph.py /path/to/impactproof-demo --scenario premium_checkout_refund
```

The viewer opens idle. The developer controls the full workflow from this dashboard.

## Step 2 — Show the code change

Show the working-tree change in `discount.py::calculate_total`: qualifying premium purchases receive the 10% discount, with the scenario's $100 purchase becoming $90.

## Step 3 — Investigate

Click **INVESTIGATE CHANGE**. Wait for the deterministic pipeline to finish before describing graph or proof results.

## Step 4 — Show predicted impact

Show **PREDICTED IMPACT** and the changed, directly affected, and indirectly affected nodes. Explain that these are static-analysis predictions of possible downstream impact, not a set of observed runtime failures.

## Step 5 — Show proof evidence

Show **PROVEN BY EXECUTION**:

| Value | Expected | Actual | Result |
| --- | ---: | ---: | --- |
| `final_amount` | $90 | $90 | PASS |
| `invoice_final_amount` | $90 | $90 | PASS |
| `refund_amount` | $90 | $100 | REGRESSION |

The only mismatch is the refund amount. The proof engine, rather than Bob, determines this status.

## Step 6 — Ask Bob to explain and fix

Select the regression evidence and click **BOB — EXPLAIN & FIX**. Review Bob's returned explanation and proposed source change against the displayed deterministic evidence. Then click **APPLY FIX** if the proposal is acceptable.

## Step 7 — Verify

Click **Verify** as a separate action. Show **FIX VERIFIED** only if the deterministic verification result is `PASS`. If the result is `REGRESSION` or `ERROR`, report that result accurately.

## Step 8 — Optional Undo

After a confirmed fix, show **UNDO BOB FIX** as an optional action. The action targets the recorded Bob/ImpactProof change; it is not run automatically. End the main demo with the verified result visible before demonstrating undo.

