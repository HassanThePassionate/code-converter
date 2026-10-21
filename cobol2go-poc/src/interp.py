"""
COBOL Subset Reference Interpreter for cobol2go-poc.

Implements exact COBOL fixed-point decimal arithmetic semantics:
1. Scaled integers: each field is an integer scaled by 10^scale.
2. Truncation toward zero when ROUNDED is absent.
3. Round half away from zero when ROUNDED is present.
4. High-order truncation modulo 10^total_digits when capacity is exceeded without SIZE ERROR.
5. ON SIZE ERROR: if overflow or zero-division occurs, target field is NOT modified,
   and the ON SIZE ERROR branch executes.
6. Unsigned fields: negative results store their absolute value (design assumption).
7. Division by zero triggers SIZE ERROR behavior.
8. COMPUTE intermediates use arbitrary precision rationals (fractions.Fraction).
"""

from __future__ import annotations
import json
from fractions import Fraction
from typing import Dict, List, Tuple, Any, Optional

from src.ir import (
    PicType, Program, Stmt, Expr, Condition,
    MoveStmt, AddStmt, SubtractStmt, MultiplyStmt, DivideStmt,
    ComputeStmt, IfStmt, DisplayStmt, StopRunStmt,
    Var, Literal, BinaryOp, UnaryOp, Comparison
)


class DivisionByZeroException(Exception):
    """Raised when an exact rational division by zero occurs."""
    pass


class CobolInterpreter:
    def __init__(self, program: Program):
        self.program = program
        self.fields_pic: Dict[str, PicType] = dict(program.fields)
        # Scaled integers
        self.state: Dict[str, int] = {name: 0 for name in self.fields_pic}
        self.display_output: List[str] = []
        self.stopped: bool = False

    def load_initial_state(self, initial: Dict[str, Any]) -> None:
        """Initialize field values from a dictionary (e.g. parsed from JSON)."""
        for k, v in initial.items():
            k_upper = k.upper()
            if k_upper in self.fields_pic:
                pic = self.fields_pic[k_upper]
                self.state[k_upper] = pic.parse_scaled(str(v))

    def eval_expr(self, expr: Expr) -> Fraction:
        """Evaluate an expression to an exact Fraction with arbitrary precision."""
        if isinstance(expr, Var):
            var_name = expr.name.upper()
            if var_name not in self.fields_pic:
                raise KeyError(f"Undefined variable in expression: {var_name}")
            pic = self.fields_pic[var_name]
            raw_val = self.state[var_name]
            return Fraction(raw_val, pic.scale_factor)

        elif isinstance(expr, Literal):
            if expr.is_numeric:
                return Fraction(expr.scaled_value, 10 ** expr.scale)
            else:
                raise TypeError(f"Cannot evaluate non-numeric literal as arithmetic expression: {expr.value_str}")

        elif isinstance(expr, UnaryOp):
            val = self.eval_expr(expr.operand)
            if expr.op == "-":
                return -val
            elif expr.op == "+":
                return val
            raise ValueError(f"Unknown unary operator: {expr.op}")

        elif isinstance(expr, BinaryOp):
            left_val = self.eval_expr(expr.left)
            right_val = self.eval_expr(expr.right)
            if expr.op == "+":
                return left_val + right_val
            elif expr.op == "-":
                return left_val - right_val
            elif expr.op == "*":
                return left_val * right_val
            elif expr.op == "/":
                if right_val == 0:
                    raise DivisionByZeroException("Division by zero in arithmetic expression")
                return left_val / right_val
            raise ValueError(f"Unknown binary operator: {expr.op}")

        raise TypeError(f"Unknown expression type: {type(expr)}")

    def store_val(
        self,
        target_name: str,
        exact_val: Optional[Fraction],
        rounded: bool,
        has_size_error_clause: bool,
        div_zero_occurred: bool = False
    ) -> bool:
        """
        Store an exact Fraction into target field according to COBOL fixed-point semantics.
        Returns: is_overflow (True if size error / overflow occurred).
        """
        pic = self.fields_pic[target_name]

        if div_zero_occurred or exact_val is None:
            # Division by zero always triggers SIZE ERROR
            return True

        # Scale the exact fraction to the target scale: V * 10^scale
        scaled_rat = exact_val * (10 ** pic.scale)
        num = scaled_rat.numerator
        den = scaled_rat.denominator
        assert den > 0

        sign = 1 if num >= 0 else -1
        abs_num = abs(num)

        if not rounded:
            # Rule 2: Without ROUNDED, truncate toward zero
            q = abs_num // den
            stored_unbounded = sign * q
        else:
            # Rule 3: With ROUNDED, round half away from zero
            q = abs_num // den
            rem = abs_num % den
            if 2 * rem >= den:
                q += 1
            stored_unbounded = sign * q

        # Rule 5: Check overflow against capacity limit (10^total_digits)
        limit = pic.capacity_limit
        is_overflow = abs(stored_unbounded) >= limit

        if is_overflow:
            if has_size_error_clause:
                # Target field is NOT modified when ON SIZE ERROR is handled
                return True
            else:
                # Rule 4: High-order truncation when no SIZE ERROR clause
                # Keep low-order digits (mod 10^total_digits)
                mag = abs(stored_unbounded) % limit
                if pic.signed:
                    stored_final = mag if stored_unbounded >= 0 else -mag
                else:
                    stored_final = mag
                self.state[target_name] = stored_final
                return True
        else:
            # No overflow: store result
            if not pic.signed and stored_unbounded < 0:
                # Rule 6: Unsigned field stores absolute value
                stored_final = abs(stored_unbounded)
            else:
                stored_final = stored_unbounded
            self.state[target_name] = stored_final
            return False

    def eval_condition(self, cond: Condition) -> bool:
        if isinstance(cond, Comparison):
            left_val = self.eval_expr(cond.left)
            right_val = self.eval_expr(cond.right)
            if cond.op == "=":
                return left_val == right_val
            elif cond.op == "!=":
                return left_val != right_val
            elif cond.op == ">":
                return left_val > right_val
            elif cond.op == "<":
                return left_val < right_val
            elif cond.op == ">=":
                return left_val >= right_val
            elif cond.op == "<=":
                return left_val <= right_val
            raise ValueError(f"Unknown comparison operator: {cond.op}")
        raise TypeError(f"Unknown condition type: {type(cond)}")

    def execute_stmts(self, stmts: List[Stmt]) -> None:
        for stmt in stmts:
            if self.stopped:
                break
            self.execute_stmt(stmt)

    def execute_stmt(self, stmt: Stmt) -> None:
        if self.stopped:
            return

        if isinstance(stmt, StopRunStmt):
            self.stopped = True
            return

        elif isinstance(stmt, MoveStmt):
            target = stmt.target.upper()
            val = self.eval_expr(stmt.source)
            self.store_val(target, val, rounded=False, has_size_error_clause=False)

        elif isinstance(stmt, ComputeStmt):
            target = stmt.target.upper()
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0
            div_zero = False
            exact_val = None
            try:
                exact_val = self.eval_expr(stmt.expr)
            except DivisionByZeroException:
                div_zero = True

            is_overflow = self.store_val(
                target, exact_val, stmt.rounded, has_se, div_zero_occurred=div_zero
            )
            if is_overflow and stmt.on_size_error:
                self.execute_stmts(stmt.on_size_error)
            elif not is_overflow and stmt.not_on_size_error:
                self.execute_stmts(stmt.not_on_size_error)

        elif isinstance(stmt, AddStmt):
            target = (stmt.giving if stmt.giving else stmt.to_target).upper()
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0

            # Sum all operands and to_target
            total = Fraction(0)
            for op in stmt.operands:
                total += self.eval_expr(op)
            total += self.eval_expr(Var(name=stmt.to_target))

            is_overflow = self.store_val(
                target, total, stmt.rounded, has_se
            )
            if is_overflow and stmt.on_size_error:
                self.execute_stmts(stmt.on_size_error)
            elif not is_overflow and stmt.not_on_size_error:
                self.execute_stmts(stmt.not_on_size_error)

        elif isinstance(stmt, SubtractStmt):
            target = (stmt.giving if stmt.giving else (stmt.from_target.name if isinstance(stmt.from_target, Var) else "")).upper()
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0

            sum_subtrahends = Fraction(0)
            for op in stmt.operands:
                sum_subtrahends += self.eval_expr(op)
            from_val = self.eval_expr(stmt.from_target)
            diff = from_val - sum_subtrahends

            is_overflow = self.store_val(
                target, diff, stmt.rounded, has_se
            )
            if is_overflow and stmt.on_size_error:
                self.execute_stmts(stmt.on_size_error)
            elif not is_overflow and stmt.not_on_size_error:
                self.execute_stmts(stmt.not_on_size_error)

        elif isinstance(stmt, MultiplyStmt):
            target = (stmt.giving if stmt.giving else (stmt.op2.name if isinstance(stmt.op2, Var) else "")).upper()
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0

            v1 = self.eval_expr(stmt.op1)
            v2 = self.eval_expr(stmt.op2)
            prod = v1 * v2

            is_overflow = self.store_val(
                target, prod, stmt.rounded, has_se
            )
            if is_overflow and stmt.on_size_error:
                self.execute_stmts(stmt.on_size_error)
            elif not is_overflow and stmt.not_on_size_error:
                self.execute_stmts(stmt.not_on_size_error)

        elif isinstance(stmt, DivideStmt):
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0
            div_zero = False
            quot = None

            if stmt.is_by:
                # DIVIDE op1 BY op2 GIVING target
                target = stmt.giving.upper()
                v1 = self.eval_expr(stmt.op1)
                v2 = self.eval_expr(stmt.op2)
                if v2 == 0:
                    div_zero = True
                else:
                    quot = v1 / v2
            else:
                # DIVIDE op1 INTO op2 [GIVING target]
                target = (stmt.giving if stmt.giving else (stmt.op2.name if isinstance(stmt.op2, Var) else "")).upper()
                v1 = self.eval_expr(stmt.op1)
                v2 = self.eval_expr(stmt.op2)
                if v1 == 0:
                    div_zero = True
                else:
                    quot = v2 / v1

            is_overflow = self.store_val(
                target, quot, stmt.rounded, has_se, div_zero_occurred=div_zero
            )
            if is_overflow and stmt.on_size_error:
                self.execute_stmts(stmt.on_size_error)
            elif not is_overflow and stmt.not_on_size_error:
                self.execute_stmts(stmt.not_on_size_error)

        elif isinstance(stmt, IfStmt):
            cond_res = self.eval_condition(stmt.cond)
            if cond_res:
                self.execute_stmts(stmt.then_branch)
            elif stmt.else_branch:
                self.execute_stmts(stmt.else_branch)

        elif isinstance(stmt, DisplayStmt):
            line_parts = []
            for item in stmt.items:
                if isinstance(item, Var):
                    var_name = item.name.upper()
                    pic = self.fields_pic[var_name]
                    raw_val = self.state[var_name]
                    line_parts.append(pic.format_scaled(raw_val))
                elif isinstance(item, Literal):
                    if item.is_numeric:
                        line_parts.append(item.value_str)
                    else:
                        line_parts.append(item.value_str)
                elif isinstance(item, str):
                    line_parts.append(item)
                else:
                    # Expr
                    val = self.eval_expr(item)
                    line_parts.append(str(val))
            line = "".join(line_parts)
            self.display_output.append(line)

        else:
            raise NotImplementedError(f"Statement execution not implemented: {type(stmt)}")

    def run(self, initial_fields: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, int], List[str]]:
        if initial_fields:
            self.load_initial_state(initial_fields)
        self.execute_stmts(self.program.body)
        return self.state, self.display_output

    def to_json_dict(self) -> Dict[str, Any]:
        """Produce final result formatted as JSON dict."""
        formatted_fields = {}
        for name, pic in self.fields_pic.items():
            formatted_fields[name] = pic.format_scaled(self.state[name])
        return {
            "fields": formatted_fields,
            "raw": dict(self.state),
            "display": list(self.display_output)
        }


def run_interpreter(program: Program, initial_fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    interp = CobolInterpreter(program)
    interp.run(initial_fields)
    return interp.to_json_dict()
