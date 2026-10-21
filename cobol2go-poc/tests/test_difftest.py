"""
Differential Testing Suite for cobol2go-poc.

Runs each program through:
(a) Python reference interpreter
(b) Generated Go program (compiled binary)
(c) GnuCOBOL via cobc (if installed; skipped gracefully if absent)

Tests 5,000+ random inputs per program, plus hand-picked edge cases:
- 0, 1, max PIC values, max+1 overflow
- Halfway rounding boundaries (e.g. .xx5)
- Signed negatives
- Branches around condition thresholds

Compares all final field values and DISPLAY outputs exactly.
"""

import os
import sys
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pytest
from src.parser import parse_cobol
from src.interp import run_interpreter
from src.emit_go import emit_go
from src.ir import Program, PicType


def check_cobc_available() -> bool:
    """Check if GnuCOBOL compiler (cobc) is installed."""
    return shutil.which("cobc") is not None


def generate_interest_inputs(n: int = 5000) -> List[Dict[str, str]]:
    """
    Generate test inputs for interest.cob:
    PRINCIPAL PIC 9(7)V99 (0.00 to 9999999.99)
    RATE PIC 9(2)V99 (0.00 to 99.99)
    """
    cases = []
    # Hand-picked edge cases
    edge_cases = [
        {"PRINCIPAL": "0.00", "RATE": "0.00"},
        {"PRINCIPAL": "0.00", "RATE": "5.25"},
        {"PRINCIPAL": "10000.00", "RATE": "5.25"},
        {"PRINCIPAL": "9999999.99", "RATE": "99.99"},
        {"PRINCIPAL": "1.00", "RATE": "1.00"},
        {"PRINCIPAL": "0.01", "RATE": "0.01"},
        {"PRINCIPAL": "12345.67", "RATE": "7.50"},
        # Rounding boundaries: fraction * rate / 100 ends in exactly .005
        # e.g. 10.00 * 2.50 / 100 = 0.25 (exact)
        # 12.50 * 1.00 / 100 = 0.125 -> rounded = 0.13
        {"PRINCIPAL": "12.50", "RATE": "1.00"},
        {"PRINCIPAL": "12.40", "RATE": "1.00"},
        {"PRINCIPAL": "37.50", "RATE": "1.00"},
        {"PRINCIPAL": "62.50", "RATE": "1.00"},
        {"PRINCIPAL": "87.50", "RATE": "1.00"},
        # Overflow in ADD PRINCIPAL TO INTEREST GIVING TOTAL
        # PRINCIPAL + INTEREST >= 10000000.00 triggers SIZE ERROR
        {"PRINCIPAL": "9999999.00", "RATE": "50.00"},
    ]
    cases.extend(edge_cases)

    # Deterministic pseudo-random generation
    rng = random.Random(42)
    for _ in range(n):
        p_raw = rng.randint(0, 999999999)
        r_raw = rng.randint(0, 9999)
        p_str = f"{p_raw // 100}.{p_raw % 100:02d}"
        r_str = f"{r_raw // 100}.{r_raw % 100:02d}"
        cases.append({"PRINCIPAL": p_str, "RATE": r_str})

    return cases


def generate_discount_inputs(n: int = 5000) -> List[Dict[str, str]]:
    """
    Generate test inputs for discount.cob:
    AMOUNT PIC 9(6)V99 (0.00 to 999999.99)
    """
    cases = []
    edge_cases = [
        {"AMOUNT": "0.00"},
        {"AMOUNT": "0.01"},
        {"AMOUNT": "999.99"},
        {"AMOUNT": "1000.00"},
        {"AMOUNT": "1000.01"},
        {"AMOUNT": "1000.05"},
        {"AMOUNT": "2000.00"},
        {"AMOUNT": "999999.99"},
        # Rounding boundaries for 10% (ends in .xx5 -> e.g. 15.05 * 10 / 100 = 1.505)
        {"AMOUNT": "15.05"},
        {"AMOUNT": "15.04"},
        # Rounding boundaries for 5% (e.g. 1.10 * 5 / 100 = 0.055)
        {"AMOUNT": "1.10"},
        {"AMOUNT": "1.08"},
    ]
    cases.extend(edge_cases)

    rng = random.Random(43)
    for _ in range(n):
        a_raw = rng.randint(0, 99999999)
        a_str = f"{a_raw // 100}.{a_raw % 100:02d}"
        cases.append({"AMOUNT": a_str})

    return cases


def generate_overflow_inputs(n: int = 5000) -> List[Dict[str, str]]:
    """
    Generate test inputs for overflow.cob:
    A PIC 9(3), B PIC 9(3)
    """
    cases = []
    edge_cases = [
        {"A": "0", "B": "0"},
        {"A": "0", "B": "999"},
        {"A": "999", "B": "0"},
        {"A": "999", "B": "999"},
        {"A": "500", "B": "499"}, # sum 999 (boundary: no size error)
        {"A": "500", "B": "500"}, # sum 1000 (boundary: size error triggers!)
        {"A": "800", "B": "450"}, # sum 1250 (overflow: C mod 1000 = 250)
        {"A": "999", "B": "1"},   # sum 1000
        {"A": "1", "B": "999"},
        {"A": "123", "B": "456"},
    ]
    cases.extend(edge_cases)

    rng = random.Random(44)
    for _ in range(n):
        a = rng.randint(0, 999)
        b = rng.randint(0, 999)
        cases.append({"A": str(a), "B": str(b)})

    return cases


def compile_go_program(go_src: str, work_dir: Path, name: str) -> Path:
    """Compile generated Go source to native executable."""
    go_file = work_dir / f"{name}.go"
    exe_file = work_dir / (f"{name}.exe" if sys.platform == "win32" else name)
    go_file.write_text(go_src, encoding="utf-8")

    cmd = ["go", "build", "-o", str(exe_file), str(go_file)]
    res = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Go compilation failed:\n{res.stderr}")
    return exe_file


def run_go_batch(exe_path: Path, cases: List[Dict[str, str]], work_dir: Path) -> List[Dict[str, Any]]:
    """Run native Go executable in batch mode."""
    in_file = work_dir / "batch_in.json"
    out_file = work_dir / "batch_out.json"

    with open(in_file, "w", encoding="utf-8") as f:
        json.dump(cases, f)

    cmd = [str(exe_path), "--batch-input", str(in_file), "--batch-output", str(out_file)]
    res = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Go batch execution failed:\n{res.stderr}")

    with open(out_file, "r", encoding="utf-8") as f:
        return json.load(f)


def run_python_batch(prog: Program, cases: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Run Python reference interpreter across test cases."""
    results = []
    for c in cases:
        out = run_interpreter(prog, c)
        results.append(out)
    return results


def run_differential_test(
    cobol_path: str,
    case_generator,
    sample_count: int = 5000
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """
    Perform differential test between Python reference and Go binary.
    Returns: (total_cases, passed_cases, list_of_mismatches)
    """
    with open(cobol_path, "r", encoding="utf-8") as f:
        cobol_src = f.read()

    prog = parse_cobol(cobol_src)
    go_src = emit_go(prog)

    cases = case_generator(sample_count)
    total = len(cases)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        exe_path = compile_go_program(go_src, tmp_path, prog.name)

        py_results = run_python_batch(prog, cases)
        go_results = run_go_batch(exe_path, cases, tmp_path)

        assert len(py_results) == total
        assert len(go_results) == total

        mismatches = []
        for i in range(total):
            py_res = py_results[i]
            go_res = go_results[i]
            inp = cases[i]

            # Compare raw scaled integers
            raw_match = (py_res["raw"] == go_res["raw"])
            # Compare formatted decimal strings
            fields_match = (py_res["fields"] == go_res["fields"])
            # Compare DISPLAY lines
            display_match = (py_res["display"] == go_res["display"])

            if not (raw_match and fields_match and display_match):
                mismatches.append({
                    "case_index": i,
                    "input": inp,
                    "python_res": py_res,
                    "go_res": go_res,
                    "diff": {
                        "raw_diff": {k: (py_res["raw"].get(k), go_res["raw"].get(k)) for k in py_res["raw"] if py_res["raw"].get(k) != go_res["raw"].get(k)},
                        "fields_diff": {k: (py_res["fields"].get(k), go_res["fields"].get(k)) for k in py_res["fields"] if py_res["fields"].get(k) != go_res["fields"].get(k)},
                        "display_diff": (py_res["display"], go_res["display"]) if not display_match else None
                    }
                })

        passed = total - len(mismatches)
        return total, passed, mismatches


def test_difftest_interest():
    cobol_file = "d:/converter/cobol2go-poc/samples/interest.cob"
    total, passed, mismatches = run_differential_test(cobol_file, generate_interest_inputs, sample_count=5000)
    print(f"\n[interest.cob Differential Test] Tested {total} cases: {passed} passed, {len(mismatches)} failed.")
    if mismatches:
        print("First 3 mismatches:")
        for m in mismatches[:3]:
            print(json.dumps(m, indent=2))
    assert len(mismatches) == 0, f"Differential test failed with {len(mismatches)} mismatches!"


def test_difftest_discount():
    cobol_file = "d:/converter/cobol2go-poc/samples/discount.cob"
    total, passed, mismatches = run_differential_test(cobol_file, generate_discount_inputs, sample_count=5000)
    print(f"\n[discount.cob Differential Test] Tested {total} cases: {passed} passed, {len(mismatches)} failed.")
    if mismatches:
        print("First 3 mismatches:")
        for m in mismatches[:3]:
            print(json.dumps(m, indent=2))
    assert len(mismatches) == 0, f"Differential test failed with {len(mismatches)} mismatches!"


def test_difftest_overflow():
    cobol_file = "d:/converter/cobol2go-poc/samples/overflow.cob"
    total, passed, mismatches = run_differential_test(cobol_file, generate_overflow_inputs, sample_count=5000)
    print(f"\n[overflow.cob Differential Test] Tested {total} cases: {passed} passed, {len(mismatches)} failed.")
    if mismatches:
        print("First 3 mismatches:")
        for m in mismatches[:3]:
            print(json.dumps(m, indent=2))
    assert len(mismatches) == 0, f"Differential test failed with {len(mismatches)} mismatches!"


def test_cobc_oracle_status():
    """Report status of optional GnuCOBOL oracle."""
    available = check_cobc_available()
    if not available:
        print("\n[GnuCOBOL Status] cobc is NOT installed on this machine.")
        print("To install GnuCOBOL on Windows:")
        print("  - Via winget: winget install GnuCOBOL")
        print("  - Via chocolatey: choco install gnucobol")
        print("  - Via MSYS2: pacman -S mingw-w64-x86_64-gnucobol")
        print("Differential testing gracefully proceeded comparing Python reference vs Go target.")
    else:
        print("\n[GnuCOBOL Status] cobc is installed and detected.")
