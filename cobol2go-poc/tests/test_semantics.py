"""
Unit tests for COBOL fixed-point semantics in reference interpreter.
"""

import pytest
from src.parser import parse_cobol
from src.interp import run_interpreter


def test_truncation_toward_zero():
    # Target PIC 99V99 (scale 2). Value = 10 / 3 = 3.33333...
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. TRUNC-TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 RES PIC 9(2)V99.
    01 RES-NEG PIC S9(2)V99.
    PROCEDURE DIVISION.
        COMPUTE RES = 10 / 3.
        COMPUTE RES-NEG = -10 / 3.
        STOP RUN.
    """
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    # Truncated toward zero: 3.33 (333 scaled) and -3.33 (-333 scaled)
    assert res["raw"]["RES"] == 333
    assert res["fields"]["RES"] == "3.33"
    assert res["raw"]["RES-NEG"] == -333
    assert res["fields"]["RES-NEG"] == "-3.33"


def test_rounding_half_away_from_zero():
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. ROUND-TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 R-UP PIC 9(2)V99.
    01 R-DOWN PIC 9(2)V99.
    01 R-NEG-UP PIC S9(2)V99.
    01 R-NEG-DOWN PIC S9(2)V99.
    PROCEDURE DIVISION.
        COMPUTE R-UP ROUNDED = 125 / 1000 + 1.
        COMPUTE R-DOWN ROUNDED = 124 / 1000 + 1.
        COMPUTE R-NEG-UP ROUNDED = -(125 / 1000 + 1).
        COMPUTE R-NEG-DOWN ROUNDED = -(124 / 1000 + 1).
        STOP RUN.
    """
    # 1 + 0.125 = 1.125 -> rounded to 2 decimals = 1.13 (half away from zero)
    # 1 + 0.124 = 1.124 -> rounded to 2 decimals = 1.12
    # -(1.125) = -1.125 -> rounded to 2 decimals = -1.13
    # -(1.124) = -1.124 -> rounded to 2 decimals = -1.12
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    assert res["raw"]["R-UP"] == 113
    assert res["raw"]["R-DOWN"] == 112
    assert res["raw"]["R-NEG-UP"] == -113
    assert res["raw"]["R-NEG-DOWN"] == -112


def test_high_order_truncation_without_size_error():
    # Capacity 9(3) is 0 to 999. Storing 1250 keeps low 3 digits -> 250.
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. OVERFLOW-NO-SE.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 A PIC 9(3).
    01 B PIC 9(3).
    01 C PIC 9(3).
    01 S PIC S9(3).
    PROCEDURE DIVISION.
        MOVE 800 TO A.
        MOVE 450 TO B.
        ADD A TO B GIVING C.
        COMPUTE S = -1250.
        STOP RUN.
    """
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    assert res["raw"]["C"] == 250  # 1250 mod 1000
    assert res["raw"]["S"] == -250 # -1250 -> magnitude mod 1000 is 250, sign preserved


def test_on_size_error_target_unmodified():
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. SE-TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 A PIC 9(3).
    01 B PIC 9(3).
    01 D PIC 9(3).
    PROCEDURE DIVISION.
        MOVE 800 TO A.
        MOVE 450 TO B.
        MOVE 777 TO D.
        ADD A TO B GIVING D
            ON SIZE ERROR
                DISPLAY "SIZE ERROR TRIPPED"
            NOT ON SIZE ERROR
                DISPLAY "UNEXPECTED SUCCESS"
        END-ADD.
        STOP RUN.
    """
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    # Overflow occurred (1250 >= 1000), so D must remain unmodified at 777
    assert res["raw"]["D"] == 777
    assert res["display"] == ["SIZE ERROR TRIPPED"]


def test_division_by_zero_triggers_size_error():
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. DIV-ZERO-TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 A PIC 9(3).
    01 B PIC 9(3).
    01 RES PIC 9(3).
    PROCEDURE DIVISION.
        MOVE 100 TO A.
        MOVE 0 TO B.
        MOVE 555 TO RES.
        DIVIDE B INTO A GIVING RES
            ON SIZE ERROR
                DISPLAY "ZERO DIVISION HANDLED"
        END-DIVIDE.
        STOP RUN.
    """
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    assert res["raw"]["RES"] == 555
    assert res["display"] == ["ZERO DIVISION HANDLED"]


def test_unsigned_field_negative_stores_absolute():
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. UNSIGNED-NEG.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 U PIC 9(3).
    PROCEDURE DIVISION.
        COMPUTE U = 50 - 120.
        STOP RUN.
    """
    # 50 - 120 = -70. Unsigned stores absolute value = 70.
    prog = parse_cobol(cob)
    res = run_interpreter(prog)
    assert res["raw"]["U"] == 70


def test_interest_cob_sample():
    cob = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. INTEREST-CALC.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 PRINCIPAL PIC 9(7)V99.
    01 RATE PIC 9(2)V99.
    01 INTEREST PIC 9(7)V99.
    01 TOTAL PIC 9(7)V99.
    PROCEDURE DIVISION.
        COMPUTE INTEREST ROUNDED = PRINCIPAL * RATE / 100.
        ADD PRINCIPAL TO INTEREST GIVING TOTAL
            ON SIZE ERROR
                DISPLAY "OVERFLOW"
        END-ADD.
        DISPLAY TOTAL.
        STOP RUN.
    """
    prog = parse_cobol(cob)
    # PRINCIPAL = 10000.00 (1000000), RATE = 5.25 (525)
    # INTEREST = 10000.00 * 5.25 / 100 = 525.00
    # TOTAL = 10000.00 + 525.00 = 10525.00
    res = run_interpreter(prog, {"PRINCIPAL": "10000.00", "RATE": "5.25"})
    assert res["fields"]["INTEREST"] == "525.00"
    assert res["fields"]["TOTAL"] == "10525.00"
    assert res["display"] == ["10525.00"]


def test_discount_cob_sample():
    with open("d:/converter/cobol2go-poc/samples/discount.cob", "r") as f:
        cob = f.read()
    prog = parse_cobol(cob)
    # Tier 1: AMOUNT > 1000 -> 10% discount
    res1 = run_interpreter(prog, {"AMOUNT": "2000.00"})
    assert res1["fields"]["DISCOUNT"] == "200.00"
    assert res1["fields"]["FINAL-AMT"] == "1800.00"
    assert res1["display"] == ["1800.00"]

    # Tier 2: AMOUNT <= 1000 -> 5% discount
    res2 = run_interpreter(prog, {"AMOUNT": "500.00"})
    assert res2["fields"]["DISCOUNT"] == "25.00"
    assert res2["fields"]["FINAL-AMT"] == "475.00"
    assert res2["display"] == ["475.00"]


def test_overflow_cob_sample():
    with open("d:/converter/cobol2go-poc/samples/overflow.cob", "r") as f:
        cob = f.read()
    prog = parse_cobol(cob)
    # A=800, B=450 -> sum=1250
    # C = 1250 mod 1000 = 250
    # D with ON SIZE ERROR triggers, D remains 0, prints "SIZE ERROR DETECTED"
    res1 = run_interpreter(prog, {"A": "800", "B": "450"})
    assert res1["raw"]["C"] == 250
    assert res1["raw"]["D"] == 0
    assert "SIZE ERROR DETECTED" in res1["display"]

    # A=200, B=300 -> sum=500
    # C = 500
    # D with NOT ON SIZE ERROR succeeds -> D=500, prints "NO SIZE ERROR"
    res2 = run_interpreter(prog, {"A": "200", "B": "300"})
    assert res2["raw"]["C"] == 500
    assert res2["raw"]["D"] == 500
    assert "NO SIZE ERROR" in res2["display"]

