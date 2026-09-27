"""
Tests for analyze.py.

Run from the mcp-server directory:
    python test_analyze.py

Covers:
  Phase 1:
  - blast radius detection (fixture/shop package)
  - repo_path does not exist
  - repo_path is not a git repository
  - no git diff (clean working tree)
  Phase 2:
  - symbol_relationships emitted for cross-file calls
  - from/to structure with file + symbol fields
  - calls via module.attr() pattern (module_map path)
  - no_diff result contains empty symbol_relationships
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
ANALYZER = os.path.join(os.path.dirname(__file__), "src", "analyze.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_analyzer(repo_path: str) -> dict:
    result = subprocess.run(
        [sys.executable, ANALYZER, repo_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise RuntimeError(f"Analyzer exited {result.returncode}")
    if result.stderr:
        print("[analyzer stderr]", result.stderr, file=sys.stderr)
    return json.loads(result.stdout)


def setup_git_repo(src_dir: str) -> tuple[str, str]:
    """
    Copy fixtures into a temp git repo, commit everything, then modify discount.py
    to simulate a working-tree change.
    Returns (repo_path, tmp_root) — call teardown(tmp_root) when done.
    """
    tmp = tempfile.mkdtemp(prefix="impactproof_test_")
    repo = os.path.join(tmp, "repo")
    shutil.copytree(src_dir, repo)

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

    # Simulate a change: append a comment to discount.py
    discount_file = os.path.join(repo, "shop", "discount.py")
    with open(discount_file, "a") as f:
        f.write("\n# changed\n")

    return repo, tmp


def teardown(tmp: str) -> None:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_blast_radius() -> None:
    """Core blast-radius test using the fixtures/shop package."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)

        print("\n=== test_blast_radius output ===")
        print(json.dumps(data, indent=2))

        assert "repo_path" in data, "result must contain repo_path"

        # changed_files is now a list of {file, symbols} dicts
        changed_files_list = [cf["file"] for cf in data["changed_files"]]
        assert "shop/discount.py" in changed_files_list, \
            f"discount.py should be in changed_files, got: {changed_files_list}"

        # Symbols should include DiscountService
        discount_entry = next(cf for cf in data["changed_files"] if cf["file"] == "shop/discount.py")
        assert "DiscountService" in discount_entry["symbols"], \
            f"Expected DiscountService in symbols, got: {discount_entry['symbols']}"

        # checkout.py directly imports discount.py
        assert "shop/checkout.py" in data["directly_affected"], \
            f"checkout.py should be directly affected, got: {data['directly_affected']}"

        # reports.py imports checkout.py → indirectly affected by discount.py change
        assert "shop/reports.py" in data["indirectly_affected"], \
            f"reports.py should be indirectly affected, got: {data['indirectly_affected']}"

        assert data["summary"]["changed_files"] >= 1
        assert data["summary"]["affected_files"] >= 2

        # relationships should explain the edges
        rel_tos = [r["to"] for r in data["relationships"]]
        assert "shop/checkout.py" in rel_tos
        assert "shop/reports.py" in rel_tos

        print("✅ test_blast_radius passed.")
    finally:
        teardown(tmp)


def test_nonexistent_repo() -> None:
    """Analyzer should return a structured error for a missing path."""
    data = run_analyzer("/tmp/impactproof_does_not_exist_xyz")
    print("\n=== test_nonexistent_repo output ===")
    print(json.dumps(data, indent=2))
    assert "error" in data, "should return error key for missing path"
    assert data["changed_files"] == []
    assert data["summary"]["changed_files"] == 0
    print("✅ test_nonexistent_repo passed.")


def test_not_a_git_repo() -> None:
    """Analyzer should return a structured error for a non-git directory."""
    tmp = tempfile.mkdtemp(prefix="impactproof_notgit_")
    try:
        data = run_analyzer(tmp)
        print("\n=== test_not_a_git_repo output ===")
        print(json.dumps(data, indent=2))
        assert "error" in data, "should return error key for non-git directory"
        assert data["changed_files"] == []
        print("✅ test_not_a_git_repo passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_diff() -> None:
    """Analyzer should return empty result when there is no working-tree diff."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        # Restore discount.py to its committed state so there is no diff
        subprocess.run(
            ["git", "checkout", "--", "shop/discount.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        data = run_analyzer(repo)
        print("\n=== test_no_diff output ===")
        print(json.dumps(data, indent=2))
        assert data["changed_files"] == [], \
            f"Expected no changed files, got: {data['changed_files']}"
        assert data["summary"]["changed_files"] == 0
        print("✅ test_no_diff passed.")
    finally:
        teardown(tmp)


# ---------------------------------------------------------------------------
# Phase 2 tests
# ---------------------------------------------------------------------------

def test_symbol_relationships_present() -> None:
    """symbol_relationships array must be present and non-empty when discount.py is changed."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)

        print("\n=== test_symbol_relationships_present ===")
        print(json.dumps(data.get("symbol_relationships", []), indent=2))

        assert "symbol_relationships" in data, \
            "result must contain symbol_relationships key"
        assert isinstance(data["symbol_relationships"], list), \
            "symbol_relationships must be a list"
        assert len(data["symbol_relationships"]) > 0, \
            "expected at least one symbol relationship"

        print("✅ test_symbol_relationships_present passed.")
    finally:
        teardown(tmp)


def test_symbol_relationship_structure() -> None:
    """Each symbol_relationship must have the correct {from, to, type, reason} shape."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)
        for rel in data["symbol_relationships"]:
            assert "from" in rel and "to" in rel, f"missing from/to: {rel}"
            assert "file" in rel["from"] and "symbol" in rel["from"], \
                f"from must have file+symbol: {rel['from']}"
            assert "file" in rel["to"] and "symbol" in rel["to"], \
                f"to must have file+symbol: {rel['to']}"
            assert rel["type"] == "calls_or_uses", \
                f"unexpected type: {rel['type']}"
            assert isinstance(rel["reason"], str) and rel["reason"], \
                f"reason must be a non-empty string: {rel}"

        print("✅ test_symbol_relationship_structure passed.")
    finally:
        teardown(tmp)


def test_checkout_calls_discount() -> None:
    """
    When discount.py is changed, checkout.checkout → discount.DiscountService
    must appear in symbol_relationships (from-import call pattern).
    """
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)
        sym_rels = data["symbol_relationships"]

        # Find the edge: checkout.py::Checkout → discount.py::DiscountService
        match = [
            r for r in sym_rels
            if r["from"]["file"].endswith("checkout.py")
            and r["to"]["file"].endswith("discount.py")
            and r["to"]["symbol"] == "DiscountService"
        ]
        assert match, (
            "Expected checkout.py.Checkout → discount.py.DiscountService in symbol_relationships.\n"
            f"Got: {json.dumps(sym_rels, indent=2)}"
        )
        print("✅ test_checkout_calls_discount passed.")
    finally:
        teardown(tmp)


def test_reports_calls_checkout() -> None:
    """
    reports.generate_report calls Checkout from checkout.py — must appear in
    symbol_relationships as an indirect symbol-level edge.
    """
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)
        sym_rels = data["symbol_relationships"]

        match = [
            r for r in sym_rels
            if r["from"]["file"].endswith("reports.py")
            and r["to"]["file"].endswith("checkout.py")
            and r["to"]["symbol"] == "Checkout"
        ]
        assert match, (
            "Expected reports.py → checkout.py::Checkout in symbol_relationships.\n"
            f"Got: {json.dumps(sym_rels, indent=2)}"
        )
        print("✅ test_reports_calls_checkout passed.")
    finally:
        teardown(tmp)


def test_summary_symbol_relationship_count() -> None:
    """summary.symbol_relationships count must match len(symbol_relationships)."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        data = run_analyzer(repo)
        expected = len(data["symbol_relationships"])
        actual = data["summary"].get("symbol_relationships")
        assert actual == expected, \
            f"summary.symbol_relationships={actual} but len(symbol_relationships)={expected}"
        print("✅ test_summary_symbol_relationship_count passed.")
    finally:
        teardown(tmp)


def test_no_diff_has_empty_symbol_relationships() -> None:
    """When there is no diff, symbol_relationships must be an empty list."""
    repo, tmp = setup_git_repo(FIXTURES_DIR)
    try:
        subprocess.run(
            ["git", "checkout", "--", "shop/discount.py"],
            cwd=repo, check=True, capture_output=True,
        )
        data = run_analyzer(repo)
        assert data.get("symbol_relationships") == [], \
            f"Expected empty symbol_relationships, got: {data.get('symbol_relationships')}"
        assert data["summary"].get("symbol_relationships") == 0
        print("✅ test_no_diff_has_empty_symbol_relationships passed.")
    finally:
        teardown(tmp)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Phase 1
    test_blast_radius()
    test_nonexistent_repo()
    test_not_a_git_repo()
    test_no_diff()
    # Phase 2
    test_symbol_relationships_present()
    test_symbol_relationship_structure()
    test_checkout_calls_discount()
    test_reports_calls_checkout()
    test_summary_symbol_relationship_count()
    test_no_diff_has_empty_symbol_relationships()
    print("\n🎉 All tests passed.")
