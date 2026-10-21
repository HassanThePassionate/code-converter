"""
Typed Intermediate Representation (IR) and Fixed-Point PIC type system for cobol2go-poc.

COBOL fixed-point semantics require exact scaled integer representation:
  - Each field has a scale s and total digits d.
  - A real value x is represented as integer I = round(x * 10^s).
  - Floating point numbers (float32/float64) are strictly prohibited.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional, Union, List, Dict


@dataclass(frozen=True)
class PicType:
    signed: bool
    integer_digits: int
    scale: int

    @property
    def total_digits(self) -> int:
        return self.integer_digits + self.scale

    @property
    def scale_factor(self) -> int:
        return 10 ** self.scale

    @property
    def capacity_limit(self) -> int:
        """Maximum exclusive magnitude: 10^total_digits."""
        return 10 ** self.total_digits

    @property
    def max_val(self) -> int:
        """Maximum representable scaled value."""
        return (10 ** self.total_digits) - 1

    @property
    def min_val(self) -> int:
        """Minimum representable scaled value."""
        if self.signed:
            return -((10 ** self.total_digits) - 1)
        return 0

    @classmethod
    def parse(cls, pic_str: str) -> "PicType":
        """
        Parse PIC strings such as:
          - 9(5)
          - 999
          - 9(7)V99
          - 9(7)V9(2)
          - S9(5)V99
          - S9(3)
          - V99
        """
        clean = pic_str.strip().upper()
        signed = clean.startswith("S")
        if signed:
            clean = clean[1:]

        def expand_repetition(part: str) -> int:
            """Expand combinations of 9 and 9(n) to count total digits."""
            total = 0
            idx = 0
            while idx < len(part):
                if part[idx] == '9':
                    idx += 1
                    if idx < len(part) and part[idx] == '(':
                        end = part.find(')', idx)
                        if end == -1:
                            raise ValueError(f"Unclosed parenthesis in PIC: {pic_str}")
                        count = int(part[idx + 1:end])
                        total += count
                        idx = end + 1
                    else:
                        total += 1
                else:
                    raise ValueError(f"Unexpected character '{part[idx]}' in PIC: {pic_str}")
            return total

        if 'V' in clean:
            int_part, frac_part = clean.split('V', 1)
            int_digits = expand_repetition(int_part) if int_part else 0
            scale = expand_repetition(frac_part) if frac_part else 0
        else:
            int_digits = expand_repetition(clean)
            scale = 0

        return cls(signed=signed, integer_digits=int_digits, scale=scale)

    def format_scaled(self, scaled_val: int) -> str:
        """Format a scaled integer value to standard decimal representation."""
        sign = "-" if scaled_val < 0 else ""
        abs_val = abs(scaled_val)
        if self.scale == 0:
            return f"{sign}{abs_val}"
        int_part = abs_val // self.scale_factor
        frac_part = abs_val % self.scale_factor
        return f"{sign}{int_part}.{frac_part:0{self.scale}d}"

    def parse_scaled(self, s: Union[str, int]) -> int:
        """
        Parse a decimal string or integer into a scaled integer without using float.
        """
        if isinstance(s, int):
            return s * self.scale_factor

        s_str = str(s).strip()
        sign = -1 if s_str.startswith('-') else 1
        clean = s_str.lstrip('+-')

        if '.' in clean:
            int_str, frac_str = clean.split('.', 1)
            int_val = int(int_str) if int_str else 0
            # Pad or truncate fractional digits to match self.scale
            if len(frac_str) < self.scale:
                frac_str = frac_str.ljust(self.scale, '0')
            else:
                frac_str = frac_str[:self.scale]
            frac_val = int(frac_str) if frac_str else 0
            return sign * (int_val * self.scale_factor + frac_val)
        else:
            int_val = int(clean) if clean else 0
            return sign * (int_val * self.scale_factor)


# =====================================================================
# AST / IR Nodes
# =====================================================================

class ASTNode:
    pass


class Expr(ASTNode):
    pass


@dataclass
class Var(Expr):
    name: str


@dataclass
class Literal(Expr):
    value_str: str
    is_numeric: bool
    # For numeric literals, scale of the literal
    scale: int = 0
    # Scaled integer representation: value_str * 10^scale
    scaled_value: int = 0

    @classmethod
    def numeric(cls, val_str: str) -> "Literal":
        clean = val_str.strip()
        sign = -1 if clean.startswith('-') else 1
        core = clean.lstrip('+-')
        if '.' in core:
            int_part, frac_part = core.split('.', 1)
            scale = len(frac_part)
            scaled = sign * (int(int_part if int_part else 0) * (10 ** scale) + int(frac_part))
        else:
            scale = 0
            scaled = sign * int(core)
        return cls(value_str=clean, is_numeric=True, scale=scale, scaled_value=scaled)

    @classmethod
    def string(cls, val_str: str) -> "Literal":
        return cls(value_str=val_str, is_numeric=False, scale=0, scaled_value=0)


@dataclass
class BinaryOp(Expr):
    op: str  # '+', '-', '*', '/'
    left: Expr
    right: Expr


@dataclass
class UnaryOp(Expr):
    op: str  # '+', '-'
    operand: Expr


class Condition(ASTNode):
    pass


@dataclass
class Comparison(Condition):
    op: str  # '=', '>', '<', '>=', '<=', 'NOT ='
    left: Expr
    right: Expr


class Stmt(ASTNode):
    pass


@dataclass
class MoveStmt(Stmt):
    source: Expr
    target: str


@dataclass
class AddStmt(Stmt):
    operands: List[Expr]
    to_target: str
    giving: Optional[str] = None
    rounded: bool = False
    on_size_error: List[Stmt] = field(default_factory=list)
    not_on_size_error: List[Stmt] = field(default_factory=list)


@dataclass
class SubtractStmt(Stmt):
    operands: List[Expr]
    from_target: Expr
    giving: Optional[str] = None
    rounded: bool = False
    on_size_error: List[Stmt] = field(default_factory=list)
    not_on_size_error: List[Stmt] = field(default_factory=list)


@dataclass
class MultiplyStmt(Stmt):
    op1: Expr
    op2: Expr
    giving: Optional[str] = None
    rounded: bool = False
    on_size_error: List[Stmt] = field(default_factory=list)
    not_on_size_error: List[Stmt] = field(default_factory=list)


@dataclass
class DivideStmt(Stmt):
    op1: Expr
    op2: Expr
    giving: Optional[str] = None
    rounded: bool = False
    on_size_error: List[Stmt] = field(default_factory=list)
    not_on_size_error: List[Stmt] = field(default_factory=list)
    is_by: bool = False  # True for DIVIDE op1 BY op2 GIVING target; False for DIVIDE op1 INTO op2 [GIVING target]


@dataclass
class ComputeStmt(Stmt):
    target: str
    expr: Expr
    rounded: bool = False
    on_size_error: List[Stmt] = field(default_factory=list)
    not_on_size_error: List[Stmt] = field(default_factory=list)


@dataclass
class IfStmt(Stmt):
    cond: Condition
    then_branch: List[Stmt]
    else_branch: List[Stmt] = field(default_factory=list)


@dataclass
class DisplayStmt(Stmt):
    items: List[Union[Expr, str]]


@dataclass
class StopRunStmt(Stmt):
    pass


@dataclass
class Program(ASTNode):
    name: str
    fields: Dict[str, PicType]
    body: List[Stmt]
