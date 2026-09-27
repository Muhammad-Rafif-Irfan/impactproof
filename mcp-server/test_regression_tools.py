#!/usr/bin/env python3
"""
Deterministic tests for regression_tools.py — EXPLAIN → FIX → VERIFY → optional UNDO.

Run from the mcp-server directory:
    python test_regression_tools.py

Requirements verified:
  1.  regression evidence can be retrieved                  (test_explain_returns_evidence)
  2.  evidence references the correct regression node       (test_explain_regression_node)
  3.  expected/actual values are preserved                  (test_explain_expected_actual)
  4.  relevant source information is included               (test_explain_source_snippets)
  5.  fix application is targeted                           (test_apply_fix_targeted)
  6.  proof scenario is unchanged after fix                 (test_scenario_file_unchanged)
  7.  verification uses the existing proof engine           (test_verify_uses_proof_engine)
  8.  successful fix produces PASS                          (test_verify_pass_after_correct_fix)
  9.  failed fix still produces REGRESSION                  (test_verify_regression_after_bad_fix)
  10. invalid execution produces ERROR                      (test_verify_error_bad_repo)
  --  apply_fix blocks test files                           (test_apply_fix_blocks_test_files)
  --  apply_fix blocks path traversal                       (test_apply_fix_blocks_traversal)
  --  apply_fix blocks syntax errors                        (test_apply_fix_syntax_error)
  --  apply_fix blocks scenario files                       (test_apply_fix_blocks_scenario)
  --  explain returns error for bad repo                    (test_explain_bad_repo)
  --  explain returns error for bad scenario                (test_explain_bad_scenario)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import textwrap as _tw

# Locate regression_tools.py in src/
HERE = Path(__file__).parent
TOOLS = HERE / "src" / "regression_tools.py"

# The scenario file lives in proof/scenarios/
ROOT = HERE.parent
SCENARIO_DIR = ROOT / "proof" / "scenarios"
SCENARIO_NAME = "premium_checkout_refund"


# ---------------------------------------------------------------------------
# Repo fixtures
# ---------------------------------------------------------------------------

def _make_regression_repo(tmp: str) -> str:
    """
    Replica of the demo repo — discount works, but refund has the bug.
    Also has the premium discount active (like the demo's committed state).
    """
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)

    def w(name: str, src: str) -> None:
        Path(repo, name).write_text(_tw.dedent(src.lstrip("\n")))

    w("discount.py", """
        def calculate_total(price: float, is_premium: bool = False) -> float:
            total = price
            if is_premium:
                total = price * 0.90
            return total
    """)
    w("invoice.py", """
        def create_invoice(original_amount, final_amount):
            return {"original_amount": original_amount, "final_amount": final_amount}
    """)
    w("payment.py", """
        from invoice import create_invoice
        def charge(final_amount, original_amount):
            invoice = create_invoice(original_amount=original_amount, final_amount=final_amount)
            return {"original_amount": original_amount, "final_amount": final_amount, "invoice": invoice}
    """)
    w("checkout.py", """
        from discount import calculate_total
        from payment import charge
        def checkout(price, is_premium=False):
            final_amount = calculate_total(price, is_premium)
            return charge(final_amount, original_amount=price)
    """)
    # BUG: refund returns original_amount instead of final_amount
    w("refund.py", """
        def refund(transaction: dict) -> float:
            return transaction["original_amount"]
    """)

    # Initialise git repo and commit everything, then change discount.py
    _git_init_and_commit(repo)
    # Simulate the discount.py change (already active, touch it to create a diff)
    discount_path = Path(repo, "discount.py")
    discount_path.write_text(discount_path.read_text() + "# changed\n")

    return repo


def _make_passing_repo(tmp: str) -> str:
    """Same as regression but refund uses final_amount (correct)."""
    repo = os.path.join(tmp, "repo_pass")
    os.makedirs(repo)

    def w(name: str, src: str) -> None:
        Path(repo, name).write_text(_tw.dedent(src.lstrip("\n")))

    w("discount.py", """
        def calculate_total(price: float, is_premium: bool = False) -> float:
            total = price
            if is_premium:
                total = price * 0.90
            return total
    """)
    w("invoice.py", """
        def create_invoice(original_amount, final_amount):
            return {"original_amount": original_amount, "final_amount": final_amount}
    """)
    w("payment.py", """
        from invoice import create_invoice
        def charge(final_amount, original_amount):
            invoice = create_invoice(original_amount=original_amount, final_amount=final_amount)
            return {"original_amount": original_amount, "final_amount": final_amount, "invoice": invoice}
    """)
    w("checkout.py", """
        from discount import calculate_total
        from payment import charge
        def checkout(price, is_premium=False):
            final_amount = calculate_total(price, is_premium)
            return charge(final_amount, original_amount=price)
    """)
    # CORRECT: refund returns final_amount
    w("refund.py", """
        def refund(transaction: dict) -> float:
            return transaction["final_amount"]
    """)

    _git_init_and_commit(repo)
    discount_path = Path(repo, "discount.py")
    discount_path.write_text(discount_path.read_text() + "# changed\n")

    return repo


def _git_init_and_commit(repo: str) -> None:
    cmds = [
        ["git", "init"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "."],
        ["git", "commit", "-m", "initial"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Tool runner helpers
# ---------------------------------------------------------------------------

def run_tools(args: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(TOOLS)] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"regression_tools.py exited {result.returncode}: {result.stderr}")
    if result.stderr.strip():
        print("[tools stderr]", result.stderr.strip(), file=sys.stderr)
    return json.loads(result.stdout)


def explain(repo: str) -> dict:
    return run_tools(["explain", repo, SCENARIO_NAME])


def apply_fix(repo: str, file_rel: str, new_source: str) -> dict:
    return run_tools(["apply_fix", repo, file_rel, new_source])


def undo_fix(repo: str) -> dict:
    return run_tools(["undo_fix", repo])


def verify(repo: str) -> dict:
    return run_tools(["verify", repo, SCENARIO_NAME])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_explain_returns_evidence() -> None:
    """1. Regression evidence can be retrieved."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        ev = explain(repo)
        print("\n=== test_explain_returns_evidence ===")
        print(json.dumps({k: v for k, v in ev.items() if k != "source_snippets"}, indent=2))
        assert ev.get("error") is None, f"unexpected error: {ev.get('error')}"
        assert "scenario" in ev
        assert "expected" in ev
        assert "actual" in ev
        assert "mismatches" in ev
        assert "changed" in ev
        assert "regression_nodes" in ev
        print("✅ test_explain_returns_evidence passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explain_regression_node() -> None:
    """2. Evidence references the correct regression node (refund.py::refund)."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        ev = explain(repo)
        rnodes = ev.get("regression_nodes", [])
        assert any(rn["symbol"] == "refund" for rn in rnodes), \
            f"Expected refund in regression_nodes, got: {rnodes}"
        print("✅ test_explain_regression_node passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explain_expected_actual() -> None:
    """3. Expected/actual values are preserved correctly."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        ev = explain(repo)
        assert ev["expected"].get("refund_amount") == 90.0, \
            f"expected refund_amount=90.0, got {ev['expected']}"
        assert ev["actual"]["refund_amount"] == 100, \
            f"actual refund_amount should be 100, got {ev['actual']}"
        print("✅ test_explain_expected_actual passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explain_source_snippets() -> None:
    """4. Relevant source information is included (snippets for changed + regression)."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        ev = explain(repo)
        snippets = ev.get("source_snippets", [])
        assert len(snippets) > 0, "Expected at least one source snippet"
        roles = {s["role"] for s in snippets}
        assert "regression" in roles, f"Expected a regression snippet, roles: {roles}"
        reg_snip = next(s for s in snippets if s["role"] == "regression")
        assert "refund" in reg_snip["source"], \
            f"Regression snippet should contain 'refund': {reg_snip['source']}"
        print("✅ test_explain_source_snippets passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_fix_targeted() -> None:
    """5. Fix application is targeted — only the specified file changes."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        fix_source = 'def refund(transaction: dict) -> float:\n    return transaction["final_amount"]\n'
        result = apply_fix(repo, "refund.py", fix_source)
        assert result.get("status") == "applied", f"Expected applied, got: {result}"
        assert result.get("file") == "refund.py"
        # Verify the file was actually written
        written = (Path(repo) / "refund.py").read_text()
        assert "final_amount" in written, "Fix should have written final_amount"
        # Other files must not have changed
        checkout_src = (Path(repo) / "checkout.py").read_text()
        assert "calculate_total" in checkout_src, "checkout.py should be unmodified"
        print("✅ test_apply_fix_targeted passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scenario_file_unchanged() -> None:
    """6. Proof scenario file is unchanged after a fix attempt."""
    scenario_path = SCENARIO_DIR / f"{SCENARIO_NAME}.json"
    original = scenario_path.read_text(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        fix_source = 'def refund(transaction: dict) -> float:\n    return transaction["final_amount"]\n'
        apply_fix(repo, "refund.py", fix_source)
        # Scenario must be byte-identical after the fix
        after = scenario_path.read_text(encoding="utf-8")
        assert original == after, "Scenario file was modified — not allowed!"
        print("✅ test_scenario_file_unchanged passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_uses_proof_engine() -> None:
    """7. Verify calls the proof engine and returns expected keys."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        result = verify(repo)
        for key in ("scenario", "status", "expected", "actual", "mismatches"):
            assert key in result, f"Missing key from verify result: {key}"
        print("✅ test_verify_uses_proof_engine passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_pass_after_correct_fix() -> None:
    """8. Correct fix produces PASS from the deterministic proof engine."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        # Apply the correct fix
        fix_source = 'def refund(transaction: dict) -> float:\n    return transaction["final_amount"]\n'
        apply_fix(repo, "refund.py", fix_source)
        result = verify(repo)
        print("\n=== test_verify_pass_after_correct_fix ===")
        print(json.dumps(result, indent=2))
        assert result["status"] == "PASS", f"Expected PASS, got: {result['status']}"
        assert result["mismatches"] == []
        print("✅ test_verify_pass_after_correct_fix passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_regression_after_bad_fix() -> None:
    """9. A wrong fix still produces REGRESSION."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        # Wrong fix — still returns original_amount (no real change)
        bad_fix = 'def refund(transaction: dict) -> float:\n    return transaction["original_amount"] + 0\n'
        apply_fix(repo, "refund.py", bad_fix)
        result = verify(repo)
        print("\n=== test_verify_regression_after_bad_fix ===")
        print(json.dumps(result, indent=2))
        assert result["status"] == "REGRESSION", f"Expected REGRESSION, got: {result['status']}"
        print("✅ test_verify_regression_after_bad_fix passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_error_bad_repo() -> None:
    """10. Non-existent repo_path produces ERROR (not a crash)."""
    result = verify("/tmp/impactproof_nonexistent_efv_xyz_abc")
    print("\n=== test_verify_error_bad_repo ===")
    print(json.dumps(result, indent=2))
    assert result.get("status") == "ERROR" or result.get("error"), \
        f"Expected ERROR, got: {result}"
    print("✅ test_verify_error_bad_repo passed.")


def test_apply_fix_blocks_test_files() -> None:
    """apply_fix must reject modifications to test files."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        result = apply_fix(repo, "test_refund.py", "# sneaky\n")
        assert "error" in result, f"Expected error blocking test file, got: {result}"
        assert "test" in result["error"].lower() or "permitted" in result["error"].lower()
        print("✅ test_apply_fix_blocks_test_files passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_fix_blocks_traversal() -> None:
    """apply_fix must reject path traversal attempts."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        result = apply_fix(repo, "../../etc/passwd", "# evil\n")
        assert "error" in result, f"Expected error for path traversal, got: {result}"
        print("✅ test_apply_fix_blocks_traversal passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_fix_syntax_error() -> None:
    """apply_fix must reject source with a Python syntax error."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        result = apply_fix(repo, "refund.py", "def refund(tx:\n    pass\n")
        assert "error" in result, f"Expected error for syntax error, got: {result}"
        assert "syntax" in result["error"].lower()
        print("✅ test_apply_fix_syntax_error passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_fix_blocks_scenario() -> None:
    """apply_fix must not modify proof scenario files inside the repo."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        # Create a fake scenario directory inside the test repo and try to write to it
        scenario_dir = Path(repo) / "proof" / "scenarios"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        result = apply_fix(repo, f"proof/scenarios/{SCENARIO_NAME}.json", "{}\n")
        assert "error" in result, f"Expected error blocking scenario file, got: {result}"
        print("✅ test_apply_fix_blocks_scenario passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_reverses_exact_bob_change() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        before = refund.read_text(encoding="utf-8")
        fixed = before.replace('transaction["original_amount"]', 'transaction["final_amount"]')
        applied = apply_fix(repo, "refund.py", fixed)
        assert applied.get("status") == "applied", applied

        result = undo_fix(repo)
        assert result.get("status") == "undone", result
        assert refund.read_text(encoding="utf-8") == before
        print("✅ test_undo_fix_reverses_exact_bob_change passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_preserves_other_file_developer_change() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_other_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        before = refund.read_text(encoding="utf-8")
        fixed = before.replace('transaction["original_amount"]', 'transaction["final_amount"]')
        assert apply_fix(repo, "refund.py", fixed).get("status") == "applied"

        note = Path(repo) / "developer_notes.py"
        note.write_text("developer_change = True\n", encoding="utf-8")
        result = undo_fix(repo)
        assert result.get("status") == "undone", result
        assert refund.read_text(encoding="utf-8") == before
        assert note.read_text(encoding="utf-8") == "developer_change = True\n"
        print("✅ test_undo_fix_preserves_other_file_developer_change passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_preserves_same_file_change_outside_bob_span() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_same_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        before = refund.read_text(encoding="utf-8")
        fixed = before.replace('transaction["original_amount"]', 'transaction["final_amount"]')
        assert apply_fix(repo, "refund.py", fixed).get("status") == "applied"

        developer_addition = "# Developer note added after Bob's fix.\n"
        with refund.open("a", encoding="utf-8") as stream:
            stream.write(developer_addition)
        result = undo_fix(repo)
        assert result.get("status") == "undone", result
        assert refund.read_text(encoding="utf-8") == before + developer_addition
        print("✅ test_undo_fix_preserves_same_file_change_outside_bob_span passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_refuses_diverged_target_without_modifying_it() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_diverged_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        before = refund.read_text(encoding="utf-8")
        fixed = before.replace('transaction["original_amount"]', 'transaction["final_amount"]')
        assert apply_fix(repo, "refund.py", fixed).get("status") == "applied"
        unexpectedly_changed = fixed.replace('transaction["final_amount"]', '42')
        refund.write_text(unexpectedly_changed, encoding="utf-8")

        result = undo_fix(repo)
        assert result.get("status") == "error", result
        assert refund.read_text(encoding="utf-8") == unexpectedly_changed
        print("✅ test_undo_fix_refuses_diverged_target_without_modifying_it passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_without_state_fails_without_modification() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_empty_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        before = refund.read_text(encoding="utf-8")
        result = undo_fix(repo)
        assert result.get("status") == "error", result
        assert refund.read_text(encoding="utf-8") == before
        print("✅ test_undo_fix_without_state_fails_without_modification passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_fix_reverts_only_most_recent_bob_fix() -> None:
    tmp = tempfile.mkdtemp(prefix="ip_undo_stack_")
    try:
        repo = _make_regression_repo(tmp)
        refund = Path(repo) / "refund.py"
        refund_before = refund.read_text(encoding="utf-8")
        refund_fixed = refund_before.replace('transaction["original_amount"]', 'transaction["final_amount"]')
        assert apply_fix(repo, "refund.py", refund_fixed).get("status") == "applied"

        invoice = Path(repo) / "invoice.py"
        invoice_before = invoice.read_text(encoding="utf-8")
        invoice_fixed = invoice_before.replace('"final_amount": final_amount', '"final_amount": final_amount + 1')
        assert apply_fix(repo, "invoice.py", invoice_fixed).get("status") == "applied"

        result = undo_fix(repo)
        assert result.get("status") == "undone", result
        assert result.get("file") == "invoice.py"
        assert invoice.read_text(encoding="utf-8") == invoice_before
        assert refund.read_text(encoding="utf-8") == refund_fixed
        print("✅ test_undo_fix_reverts_only_most_recent_bob_fix passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explain_bad_repo() -> None:
    """explain must return an error for a non-existent repo."""
    result = explain("/tmp/impactproof_nonexistent_efv_explain")
    assert "error" in result, f"Expected error for bad repo, got: {result}"
    print("✅ test_explain_bad_repo passed.")


def test_explain_bad_scenario() -> None:
    """explain must return an error for an unknown scenario name."""
    tmp = tempfile.mkdtemp(prefix="ip_efv_")
    try:
        repo = _make_regression_repo(tmp)
        result = run_tools(["explain", repo, "nonexistent_scenario_xyz"])
        assert "error" in result, f"Expected error for bad scenario, got: {result}"
        print("✅ test_explain_bad_scenario passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_explain_returns_evidence()
    test_explain_regression_node()
    test_explain_expected_actual()
    test_explain_source_snippets()
    test_apply_fix_targeted()
    test_scenario_file_unchanged()
    test_verify_uses_proof_engine()
    test_verify_pass_after_correct_fix()
    test_verify_regression_after_bad_fix()
    test_verify_error_bad_repo()
    test_apply_fix_blocks_test_files()
    test_apply_fix_blocks_traversal()
    test_apply_fix_syntax_error()
    test_apply_fix_blocks_scenario()
    test_undo_fix_reverses_exact_bob_change()
    test_undo_fix_preserves_other_file_developer_change()
    test_undo_fix_preserves_same_file_change_outside_bob_span()
    test_undo_fix_refuses_diverged_target_without_modifying_it()
    test_undo_fix_without_state_fails_without_modification()
    test_undo_fix_reverts_only_most_recent_bob_fix()
    test_explain_bad_repo()
    test_explain_bad_scenario()
    print("\n🎉 All regression tools tests passed.")
