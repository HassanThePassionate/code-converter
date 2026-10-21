# cobol2go-poc

A proof-of-concept transpiler translating a defined subset of COBOL fixed-point arithmetic programs to Go (1.21+), backed by two layers of formal and empirical verification:
1. **Differential Testing**: Comparing the reference interpreter against the native compiled Go target across 5,000+ random inputs and edge cases per program (plus GnuCOBOL oracle when installed).
2. **Translation Validation (Z3)**: SMT-LIB formal equivalence check proving that the emitted Go arithmetic logic matches COBOL semantics for **all** inputs in the declared PIC ranges.

> **Hard Rules & Honest Boundaries**
> - **Zero Floats**: No `float32`, `float64`, or Python `float` are used anywhere. All calculations use scaled integers and arbitrary-precision rationals (`math/big` in Go, `fractions.Fraction` in Python, integer theory in Z3).
> - **No False Claims**: We do **not** claim "100% correct" or "mathematically guaranteed". Translation validation verifies the arithmetic translation logic over the declared PIC bounds; it does **not** verify the Go compiler itself, parser correctness, OS runtime execution, or features outside the v0.1 subset.

---

## 1. COBOL v0.1 Subset

- **Divisions**: `IDENTIFICATION DIVISION` (PROGRAM-ID), `DATA DIVISION` (`WORKING-STORAGE SECTION`), `PROCEDURE DIVISION`.
- **Data Items**: Level `01` elementary items with PIC clauses:
  - `9(n)`, `999` (unsigned integer)
  - `9(n)V9(m)`, `99V99` (unsigned fixed-point)
  - `S9(n)` (signed integer)
  - `S9(n)V9(m)` (signed fixed-point)
- **Statements**:
  - `MOVE <source> TO <target>`
  - `ADD ... TO ... [GIVING] [ROUNDED] [ON SIZE ERROR] [NOT ON SIZE ERROR]`
  - `SUBTRACT ... FROM ... [GIVING] [ROUNDED] [ON SIZE ERROR] [NOT ON SIZE ERROR]`
  - `MULTIPLY ... BY ... [GIVING] [ROUNDED] [ON SIZE ERROR] [NOT ON SIZE ERROR]`
  - `DIVIDE ... INTO/BY ... [GIVING] [ROUNDED] [ON SIZE ERROR] [NOT ON SIZE ERROR]`
  - `COMPUTE ... [ROUNDED] = <expr> [ON SIZE ERROR] [NOT ON SIZE ERROR]`
  - `IF <cond> THEN ... [ELSE ...] END-IF`
  - `DISPLAY ...`
  - `STOP RUN`
- **Semantics**:
  - Fixed-point decimal with scaled integers `(total_digits, scale, signed)`.
  - Without `ROUNDED`: result truncated toward zero at target scale.
  - With `ROUNDED`: round half away from zero.
  - High-order truncation: if capacity exceeded without `SIZE ERROR`, retain low-order digits modulo $10^{\text{total\_digits}}$ (preserving sign if signed).
  - `ON SIZE ERROR`: target field is **not** modified if overflow or division by zero occurs.
  - Unsigned fields: negative results store their absolute value modulo capacity.
  - Intermediates: arbitrary precision rationals, rounding/truncation only applied on store.

---

## 2. Installation & Setup

### Prerequisites
- Python 3.11+
- Go 1.21+
- *(Optional)* GnuCOBOL (`cobc`) for 3-way differential testing. If missing:
  - Windows: `winget install GnuCOBOL` or `choco install gnucobol`
  - Linux: `sudo apt install open-cobol`

### Install Dependencies
```bash
cd cobol2go-poc
pip install -r requirements.txt
```

---

## 3. Usage & CLI Commands

All commands are available through `python -m src.cli`:

### 1. Translate COBOL to Go
```bash
python -m src.cli translate samples/interest.cob -o samples/interest.go
```

### 2. Run Reference Interpreter
```bash
python -m src.cli run samples/interest.cob --input samples/test_in.json --output out.json
```

### 3. Formally Verify with Z3
```bash
python -m src.cli verify samples/interest.cob
```
Output:
```
Result: PROVED EQUIVALENT (UNSAT)
Fields checked: PRINCIPAL, RATE, INTEREST, TOTAL
Input PIC Domains:
  PRINCIPAL: [0, 999999999] (scaled)
  RATE: [0, 9999] (scaled)
  ...
```

### 4. Catch Deliberate Emitter Bug with Z3
Inject a deliberate bug (e.g. truncation instead of `ROUNDED`) and inspect the counterexample:
```bash
python -m src.cli verify samples/interest.cob --buggy truncate_instead_of_round
```
Output:
```
Result: DISCREPANCY DETECTED (SAT)
Counterexample Found:
{
  "PRINCIPAL": 999999991,
  "RATE": 9902,
  "_ref_output": { "INTEREST": 19999109 },
  "_go_output":  { "INTEREST": 19999108 }
}
```

### 5. Run Differential Testing
```bash
python -m src.cli difftest samples/interest.cob --samples 5000
```

---

## 4. Running the Complete Test Suite

Run all 21 unit, semantic, translation validation, and differential tests:
```bash
pytest -v
```
All tests run in ~10 seconds.
