# cobol2go-poc

A proof-of-concept transpiler translating a defined subset of COBOL fixed-point arithmetic programs to Go (1.21+), backed by two layers of formal and empirical verification:
1. **Differential Testing**: Comparing the reference interpreter against the native compiled Go target across 5,000+ random inputs and edge cases per program (plus GnuCOBOL oracle when installed).
2. **Translation Validation (Z3)**: SMT-LIB formal equivalence check proving that the emitted Go arithmetic logic matches COBOL semantics for **all** inputs in the declared PIC ranges.

> **Hard Rules & Honest Boundaries**
> - **Zero Floats**: No `float32`, `float64`, or Python `float` are used anywhere. All calculations use scaled integers and arbitrary-precision rationals (`math/big` in Go, `fractions.Fraction` in Python, integer theory in Z3).
> - **No False Claims**: We do **not** claim "100% correct" or "mathematically guaranteed". Translation validation verifies the arithmetic translation logic over the declared PIC bounds; it does **not** verify the Go compiler itself, parser correctness, OS runtime execution, or features outside the v0.1 subset.

---

## Quick Start
```bash
# In this directory or in cobol2go-poc:
pip install -r requirements.txt
cd cobol2go-poc
pytest -v
```

See [cobol2go-poc/README.md](file:///d:/converter/cobol2go-poc/README.md) for full documentation and CLI commands.
