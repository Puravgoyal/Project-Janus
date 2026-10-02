"""
Project Janus — E2E Test Suite Runner
Standalone CLI test runner executing test tiers 1 through 4 with comprehensive reporting.
"""

import sys
import os
import argparse
import time
import json
import unittest
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class JanusTestResult(unittest.TestResult):
    """Custom TestResult collecting granular tier and timing metrics."""
    def __init__(self, verbose: bool = False, bail: bool = False):
        super().__init__()
        self.verbose = verbose
        self.bail = bail
        self.test_records: List[Dict[str, Any]] = []
        self._current_test_start = 0.0

    def startTest(self, test: unittest.TestCase):
        super().startTest(test)
        self._current_test_start = time.perf_counter()
        if self.verbose:
            test_name = test.shortDescription() or test.id().split(".")[-1]
            sys.stdout.write(f"  RUN: {test_name:<60} ... ")
            sys.stdout.flush()

    def addSuccess(self, test: unittest.TestCase):
        super().addSuccess(test)
        duration = time.perf_counter() - self._current_test_start
        self.test_records.append({
            "id": test.id(),
            "name": test.shortDescription() or test.id().split(".")[-1],
            "class": test.__class__.__name__,
            "status": "PASS",
            "duration": round(duration, 4),
            "error": None
        })
        if self.verbose:
            sys.stdout.write(f"\033[92mPASS\033[0m ({duration:.3f}s)\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(".")
            sys.stdout.flush()

    def addFailure(self, test: unittest.TestCase, err):
        super().addFailure(test, err)
        duration = time.perf_counter() - self._current_test_start
        err_msg = self._exc_info_to_string(err, test)
        self.test_records.append({
            "id": test.id(),
            "name": test.shortDescription() or test.id().split(".")[-1],
            "class": test.__class__.__name__,
            "status": "FAIL",
            "duration": round(duration, 4),
            "error": err_msg
        })
        if self.verbose:
            sys.stdout.write(f"\033[91mFAIL\033[0m ({duration:.3f}s)\n")
            sys.stdout.flush()
        else:
            sys.stdout.write("F")
            sys.stdout.flush()
        if self.bail:
            self.stop()

    def addError(self, test: unittest.TestCase, err):
        super().addError(test, err)
        duration = time.perf_counter() - self._current_test_start
        err_msg = self._exc_info_to_string(err, test)
        self.test_records.append({
            "id": test.id(),
            "name": test.shortDescription() or test.id().split(".")[-1],
            "class": test.__class__.__name__,
            "status": "ERROR",
            "duration": round(duration, 4),
            "error": err_msg
        })
        if self.verbose:
            sys.stdout.write(f"\033[91mERROR\033[0m ({duration:.3f}s)\n")
            sys.stdout.flush()
        else:
            sys.stdout.write("E")
            sys.stdout.flush()
        if self.bail:
            self.stop()

    def addSkip(self, test: unittest.TestCase, reason: str):
        super().addSkip(test, reason)
        duration = time.perf_counter() - self._current_test_start
        self.test_records.append({
            "id": test.id(),
            "name": test.shortDescription() or test.id().split(".")[-1],
            "class": test.__class__.__name__,
            "status": "SKIP",
            "duration": round(duration, 4),
            "reason": reason
        })
        if self.verbose:
            sys.stdout.write(f"\033[93mSKIP\033[0m ({reason})\n")
            sys.stdout.flush()
        else:
            sys.stdout.write("S")
            sys.stdout.flush()


def discover_tier_suite(tier: int) -> unittest.TestSuite:
    """Loads the test suite for a given tier number."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    tier_map = {
        1: "e2e_tests.test_tier1_features",
        2: "e2e_tests.test_tier2_boundaries",
        3: "e2e_tests.test_tier3_interactions",
        4: "e2e_tests.test_tier4_scenarios",
    }
    module_name = tier_map.get(tier)
    if not module_name:
        raise ValueError(f"Invalid tier {tier}. Valid tiers are 1, 2, 3, 4.")
    try:
        mod_suite = loader.loadTestsFromName(module_name)
        suite.addTests(mod_suite)
    except Exception as e:
        print(f"\033[91m[ERROR]\033[0m Failed loading {module_name}: {e}")
    return suite


def run_tier(tier: int, verbose: bool = False, bail: bool = False) -> Tuple[JanusTestResult, float]:
    """Executes a single test tier and returns results and elapsed duration."""
    tier_names = {
        1: "Tier 1: Feature Coverage (28 Features, >=5 tests/feature)",
        2: "Tier 2: Boundary & Corner Cases (Limits, Edge Cases, Errors)",
        3: "Tier 3: Pairwise Cross-Feature Interactions",
        4: "Tier 4: Real-World Application Scenarios",
    }
    title = tier_names.get(tier, f"Tier {tier}")
    print(f"\n{'='*75}")
    print(f"Executing {title}")
    print(f"{'='*75}")

    suite = discover_tier_suite(tier)
    test_count = suite.countTestCases()
    print(f"Discovered {test_count} tests in Tier {tier}.")

    result = JanusTestResult(verbose=verbose, bail=bail)
    start_time = time.perf_counter()
    if not verbose:
        sys.stdout.write("Running tests: ")
        sys.stdout.flush()

    suite.run(result)

    if not verbose:
        sys.stdout.write("\n")
        sys.stdout.flush()

    elapsed = time.perf_counter() - start_time
    return result, elapsed


def print_summary_table(tier_results: Dict[int, Tuple[JanusTestResult, float]]):
    """Prints a structured summary table of all executed tiers."""
    print(f"\n{'-'*75}")
    print(f"{'Tier':<10} | {'Tests':<8} | {'Passed':<8} | {'Failed':<8} | {'Errors':<8} | {'Skipped':<8} | {'Time (s)':<8}")
    print(f"{'-'*75}")

    total_tests = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_skipped = 0
    total_time = 0.0

    for tier, (res, duration) in sorted(tier_results.items()):
        run_count = res.testsRun
        failed_count = len(res.failures)
        error_count = len(res.errors)
        skip_count = len(res.skipped)
        passed_count = run_count - failed_count - error_count

        total_tests += run_count
        total_passed += passed_count
        total_failed += failed_count
        total_errors += error_count
        total_skipped += skip_count
        total_time += duration

        print(f"Tier {tier:<5} | {run_count:<8} | {passed_count:<8} | {failed_count:<8} | {error_count:<8} | {skip_count:<8} | {duration:<8.2f}")

    print(f"{'-'*75}")
    print(f"{'TOTAL':<10} | {total_tests:<8} | {total_passed:<8} | {total_failed:<8} | {total_errors:<8} | {total_skipped:<8} | {total_time:<8.2f}")
    print(f"{'-'*75}\n")


def print_failures(tier_results: Dict[int, Tuple[JanusTestResult, float]]):
    """Prints detailed stack traces of all failures and errors."""
    has_failures = False
    for tier, (res, _) in sorted(tier_results.items()):
        if res.failures or res.errors:
            if not has_failures:
                print("\n" + "#"*75)
                print("FAILURE & ERROR DIAGNOSTICS")
                print("#"*75)
                has_failures = True

            for test, err in res.failures:
                print(f"\n[FAIL] Tier {tier} - {test.id()}:")
                print(f"{err}")
            for test, err in res.errors:
                print(f"\n[ERROR] Tier {tier} - {test.id()}:")
                print(f"{err}")


def save_json_report(filepath: str, tier_results: Dict[int, Tuple[JanusTestResult, float]]):
    """Outputs test results to a JSON file."""
    report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tiers": {},
        "summary": {
            "total_tests": 0,
            "total_passed": 0,
            "total_failed": 0,
            "total_errors": 0,
            "total_skipped": 0,
            "total_duration_seconds": 0.0
        }
    }

    for tier, (res, duration) in sorted(tier_results.items()):
        passed = res.testsRun - len(res.failures) - len(res.errors)
        report["tiers"][f"tier_{tier}"] = {
            "tests_run": res.testsRun,
            "passed": passed,
            "failed": len(res.failures),
            "errors": len(res.errors),
            "skipped": len(res.skipped),
            "duration_seconds": round(duration, 4),
            "records": res.test_records
        }
        report["summary"]["total_tests"] += res.testsRun
        report["summary"]["total_passed"] += passed
        report["summary"]["total_failed"] += len(res.failures)
        report["summary"]["total_errors"] += len(res.errors)
        report["summary"]["total_skipped"] += len(res.skipped)
        report["summary"]["total_duration_seconds"] += duration

    report["summary"]["total_duration_seconds"] = round(report["summary"]["total_duration_seconds"], 4)
    out_path = Path(filepath).resolve()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"JSON test report saved to: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Project Janus E2E Test Suite Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run specific test tier (1, 2, 3, or 4)")
    parser.add_argument("--all", action="store_true", help="Run all 4 test tiers")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output with individual test names")
    parser.add_argument("--bail", action="store_true", help="Stop execution on first failure")
    parser.add_argument("--json-report", type=str, help="Save structured results report to specified JSON file")

    args = parser.parse_args()

    # Determine which tiers to execute
    if args.tier:
        tiers_to_run = [args.tier]
    else:
        # Default to all tiers if --all or no tier specified
        tiers_to_run = [1, 2, 3, 4]

    print("\n" + "="*75)
    print("Project Janus — End-to-End Test Suite Execution")
    print(f"Tiers: {tiers_to_run} | Verbose: {args.verbose} | Bail: {args.bail}")
    print("="*75)

    tier_results: Dict[int, Tuple[JanusTestResult, float]] = {}
    any_failure = False

    for tier in tiers_to_run:
        res, duration = run_tier(tier, verbose=args.verbose, bail=args.bail)
        tier_results[tier] = (res, duration)
        if res.failures or res.errors:
            any_failure = True
            if args.bail:
                print("\n\033[91m[BAIL]\033[0m Execution stopped due to failure.")
                break

    print_summary_table(tier_results)
    print_failures(tier_results)

    if args.json_report:
        save_json_report(args.json_report, tier_results)

    if any_failure:
        print("\033[91mRESULT: Test Suite FAILED.\033[0m\n")
        sys.exit(1)
    else:
        print("\033[92mRESULT: All Tests PASSED successfully.\033[0m\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
