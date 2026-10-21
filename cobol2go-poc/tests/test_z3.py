"""
Tests for Z3-based translation validation in cobol2go-poc.
"""

import pytest
from src.parser import parse_cobol
from src.verify_z3 import verify_translation


def test_z3_verify_interest():
    with open("d:/converter/cobol2go-poc/samples/interest.cob") as f:
        prog = parse_cobol(f.read())

    # Correct translation should prove equivalent (UNSAT)
    res = verify_translation(prog)
    assert res.verified is True, f"Failed to prove equivalence: {res.counterexample}"
    assert res.counterexample is None


def test_z3_detects_buggy_emitter_variant():
    with open("d:/converter/cobol2go-poc/samples/interest.cob") as f:
        prog = parse_cobol(f.read())

    # Buggy variant: emitter generates truncation instead of ROUNDED
    res = verify_translation(prog, buggy_variant="truncate_instead_of_round")
    assert res.verified is False, "Z3 failed to catch deliberately buggy emitter!"
    assert res.counterexample is not None, "Expected concrete counterexample from Z3"

    # Verify counterexample has differing outputs
    ref_out = res.counterexample["_ref_output"]
    go_out = res.counterexample["_go_output"]
    assert ref_out["INTEREST"] != go_out["INTEREST"], (
        f"Counterexample did not exhibit difference: ref={ref_out}, go={go_out}"
    )
    print("\n[Z3 Translation Validation] Deliberately buggy variant caught!")
    print(f"Counterexample inputs: PRINCIPAL={res.counterexample.get('PRINCIPAL')}, RATE={res.counterexample.get('RATE')}")
    print(f"Reference INTEREST (ROUNDED): {ref_out['INTEREST']}")
    print(f"Buggy Go INTEREST (TRUNCATED): {go_out['INTEREST']}")


def test_z3_verify_discount():
    with open("d:/converter/cobol2go-poc/samples/discount.cob") as f:
        prog = parse_cobol(f.read())

    res = verify_translation(prog)
    assert res.verified is True, f"Failed to prove equivalence: {res.counterexample}"
    assert res.counterexample is None


def test_z3_verify_overflow():
    with open("d:/converter/cobol2go-poc/samples/overflow.cob") as f:
        prog = parse_cobol(f.read())

    res = verify_translation(prog)
    assert res.verified is True, f"Failed to prove equivalence: {res.counterexample}"
    assert res.counterexample is None
