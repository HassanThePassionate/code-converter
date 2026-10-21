"""
Command Line Interface (CLI) for cobol2go-poc.

Usage:
  python -m src.cli translate <file.cob> [-o <out.go>]
  python -m src.cli run <file.cob> [--input <in.json>] [--output <out.json>]
  python -m src.cli verify <file.cob> [--buggy <variant>]
  python -m src.cli difftest <file.cob> [--samples <N>]
"""

from __future__ import annotations
import sys
import json
import argparse
from pathlib import Path

from src.parser import parse_cobol
from src.interp import run_interpreter
from src.emit_go import emit_go
from src.verify_z3 import verify_translation


def cmd_translate(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    cobol_src = path.read_text(encoding="utf-8")
    program = parse_cobol(cobol_src)
    go_code = emit_go(program, buggy_variant=args.buggy)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(go_code, encoding="utf-8")
        print(f"Emitted Go source written to: {args.output}")
    else:
        print(go_code)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    cobol_src = path.read_text(encoding="utf-8")
    program = parse_cobol(cobol_src)

    initial_fields = {}
    if args.input:
        in_path = Path(args.input)
        if in_path.exists():
            with open(in_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "fields" in data and isinstance(data["fields"], dict):
                    initial_fields = data["fields"]
                elif isinstance(data, dict):
                    initial_fields = data

    result = run_interpreter(program, initial_fields)

    json_str = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Result written to: {args.output}")
    else:
        print(json_str)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    cobol_src = path.read_text(encoding="utf-8")
    program = parse_cobol(cobol_src)

    print(f"Running Z3 Translation Validation on: {path.name} (Program: {program.name})")
    if args.buggy:
        print(f"Applying deliberately buggy emitter variant: '{args.buggy}'")

    res = verify_translation(program, buggy_variant=args.buggy)

    print("-" * 60)
    print(f"Result: {'PROVED EQUIVALENT (UNSAT)' if res.verified else 'DISCREPANCY DETECTED (SAT)'}")
    print(f"Fields checked: {', '.join(res.checked_fields)}")
    print("Input PIC Domains:")
    for k, v in res.domain.items():
        print(f"  {k}: {v}")

    if res.verified:
        print("\nFormal Guarantee:")
        print("  SMT translation validation proved that the emitted Go arithmetic logic")
        print("  is functionally identical to COBOL semantics for ALL inputs within the")
        print("  declared PIC ranges.")
        print("\nExplicit Non-Claims:")
        print("  - This validates the arithmetic translation logic, NOT the Go compiler,")
        print("    NOT the parser, and NOT loops or unchecked external runtime factors.")
        return 0
    else:
        print("\nCounterexample Found:")
        if res.counterexample:
            print(json.dumps(res.counterexample, indent=2))
        return 2


def cmd_difftest(args: argparse.Namespace) -> int:
    from tests.test_difftest import (
        run_differential_test,
        generate_interest_inputs,
        generate_discount_inputs,
        generate_overflow_inputs,
        check_cobc_available
    )

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    fname = path.name.lower()
    if "interest" in fname:
        generator = generate_interest_inputs
    elif "discount" in fname:
        generator = generate_discount_inputs
    else:
        generator = generate_overflow_inputs

    sample_count = args.samples or 5000
    print(f"Running Differential Test on {path.name} ({sample_count} samples + edge cases)...")
    total, passed, mismatches = run_differential_test(str(path), generator, sample_count=sample_count)

    print("-" * 60)
    print(f"Total Cases: {total}")
    print(f"Passed:      {passed}")
    print(f"Failed:      {len(mismatches)}")

    if check_cobc_available():
        print("GnuCOBOL (cobc): Available as 3rd reference.")
    else:
        print("GnuCOBOL (cobc): NOT installed on this machine (skipped gracefully).")
        print("  To install: 'winget install GnuCOBOL' or 'choco install gnucobol'")

    if mismatches:
        print("\nMismatch Summary (first 3):")
        for m in mismatches[:3]:
            print(json.dumps(m, indent=2))
        return 1

    print("\nSUCCESS: All outputs (raw scaled values, formatted fields, DISPLAY) matched 100%!")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cobol2go-poc",
        description="Proof-of-Concept COBOL to Go transpiler with Z3 validation and differential testing"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # translate
    p_trans = subparsers.add_parser("translate", help="Translate COBOL to Go")
    p_trans.add_argument("file", help="Path to COBOL source file (.cob)")
    p_trans.add_argument("-o", "--output", help="Output Go file path")
    p_trans.add_argument("--buggy", choices=["truncate_instead_of_round"], help="Inject deliberate emitter bug")

    # run
    p_run = subparsers.add_parser("run", help="Run COBOL with Python reference interpreter")
    p_run.add_argument("file", help="Path to COBOL source file (.cob)")
    p_run.add_argument("--input", help="Path to JSON input file")
    p_run.add_argument("--output", help="Path to JSON output file")

    # verify
    p_ver = subparsers.add_parser("verify", help="Formally verify translation with Z3 SMT solver")
    p_ver.add_argument("file", help="Path to COBOL source file (.cob)")
    p_ver.add_argument("--buggy", choices=["truncate_instead_of_round"], help="Inject deliberate emitter bug to test detection")

    # difftest
    p_diff = subparsers.add_parser("difftest", help="Run differential testing against Go target")
    p_diff.add_argument("file", help="Path to COBOL source file (.cob)")
    p_diff.add_argument("--samples", type=int, default=5000, help="Number of random inputs (default: 5000)")

    args = parser.parse_args()

    if args.command == "translate":
        return cmd_translate(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "difftest":
        return cmd_difftest(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
