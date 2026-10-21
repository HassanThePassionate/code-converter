"""
Unit tests for COBOL parser and PIC type system.
"""

import pytest
from src.ir import PicType, MoveStmt, AddStmt, ComputeStmt, IfStmt, DisplayStmt, StopRunStmt
from src.parser import parse_cobol


def test_pic_parsing():
    p1 = PicType.parse("9(7)V99")
    assert not p1.signed
    assert p1.integer_digits == 7
    assert p1.scale == 2
    assert p1.total_digits == 9
    assert p1.scale_factor == 100
    assert p1.capacity_limit == 1_000_000_000

    p2 = PicType.parse("S9(5)V9(3)")
    assert p2.signed
    assert p2.integer_digits == 5
    assert p2.scale == 3
    assert p2.total_digits == 8

    p3 = PicType.parse("999")
    assert not p3.signed
    assert p3.integer_digits == 3
    assert p3.scale == 0

    p4 = PicType.parse("S9(3)")
    assert p4.signed
    assert p4.integer_digits == 3
    assert p4.scale == 0


def test_parse_interest_cob():
    source = """
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
    prog = parse_cobol(source)
    assert prog.name == "INTEREST-CALC"
    assert len(prog.fields) == 4
    assert prog.fields["PRINCIPAL"].total_digits == 9
    assert len(prog.body) == 4
    assert isinstance(prog.body[0], ComputeStmt)
    assert prog.body[0].rounded is True
    assert isinstance(prog.body[1], AddStmt)
    assert prog.body[1].giving == "TOTAL"
    assert len(prog.body[1].on_size_error) == 1
    assert isinstance(prog.body[2], DisplayStmt)
    assert isinstance(prog.body[3], StopRunStmt)


def test_parse_discount_cob():
    source = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. DISCOUNT-CALC.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 AMOUNT PIC 9(6)V99.
    01 DISCOUNT PIC S9(5)V99.
    01 FINAL-AMT PIC 9(6)V99.
    PROCEDURE DIVISION.
        IF AMOUNT > 1000
            COMPUTE DISCOUNT ROUNDED = AMOUNT * 10 / 100
        ELSE
            COMPUTE DISCOUNT ROUNDED = AMOUNT * 5 / 100
        END-IF.
        SUBTRACT DISCOUNT FROM AMOUNT GIVING FINAL-AMT.
        DISPLAY FINAL-AMT.
        STOP RUN.
    """
    prog = parse_cobol(source)
    assert prog.name == "DISCOUNT-CALC"
    assert len(prog.fields) == 3
    assert prog.fields["DISCOUNT"].signed is True
    assert isinstance(prog.body[0], IfStmt)
    assert len(prog.body[0].then_branch) == 1
    assert len(prog.body[0].else_branch) == 1


def test_parse_overflow_cob():
    source = """
    IDENTIFICATION DIVISION.
    PROGRAM-ID. OVERFLOW-TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 A PIC 9(3).
    01 B PIC 9(3).
    01 C PIC 9(3).
    01 D PIC 9(3).
    PROCEDURE DIVISION.
        ADD A TO B GIVING C.
        ADD A TO B GIVING D
            ON SIZE ERROR
                DISPLAY "SIZE ERROR DETECTED"
            NOT ON SIZE ERROR
                DISPLAY "NO SIZE ERROR"
        END-ADD.
        DISPLAY C.
        DISPLAY D.
        STOP RUN.
    """
    prog = parse_cobol(source)
    assert prog.name == "OVERFLOW-TEST"
    assert isinstance(prog.body[1], AddStmt)
    assert len(prog.body[1].on_size_error) == 1
    assert len(prog.body[1].not_on_size_error) == 1
