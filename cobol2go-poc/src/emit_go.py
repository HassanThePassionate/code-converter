"""
Go Code Generator for cobol2go-poc.

Translates typed IR into Go 1.21+ source code.
Hard constraints enforced:
1. Scaled integers (int64) for all storage.
2. Arbitrary-precision exact rational arithmetic (math/big) for intermediates.
3. ZERO floating point (float32/float64 prohibited).
4. CLI flag parsing for --input <file> and --output <file>.
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Union
from src.ir import (
    PicType, Program, Stmt, Expr, Condition,
    MoveStmt, AddStmt, SubtractStmt, MultiplyStmt, DivideStmt,
    ComputeStmt, IfStmt, DisplayStmt, StopRunStmt,
    Var, Literal, BinaryOp, UnaryOp, Comparison
)


def sanitize_ident(name: str) -> str:
    """Convert COBOL identifier (e.g. FINAL-AMT) to Go identifier (e.g. f_FINAL_AMT)."""
    return "f_" + name.replace("-", "_")


class GoEmitter:
    def __init__(self, program: Program, buggy_variant: Optional[str] = None):
        self.program = program
        self.fields = program.fields
        self.buggy_variant = buggy_variant  # Used for testing translation validation detection
        self.indent_level = 1

    def indent(self) -> str:
        return "\t" * self.indent_level

    def emit_expr(self, expr: Expr) -> tuple[str, str]:
        """
        Emits Go code for an expression.
        Returns: (rat_expr_str, div_zero_check_var)
        """
        if isinstance(expr, Var):
            var_name = expr.name.upper()
            pic = self.fields[var_name]
            go_var = sanitize_ident(var_name)
            return f"ratFromField({go_var}, {pic.scale})", "false"

        elif isinstance(expr, Literal):
            if expr.is_numeric:
                return f"ratFromLiteral({expr.scaled_value}, {expr.scale})", "false"
            else:
                raise ValueError(f"Non-numeric literal in arithmetic expression: {expr.value_str}")

        elif isinstance(expr, UnaryOp):
            sub_expr, dz = self.emit_expr(expr.operand)
            if expr.op == "-":
                return f"new(big.Rat).Neg({sub_expr})", dz
            elif expr.op == "+":
                return sub_expr, dz
            raise ValueError(f"Unknown unary operator: {expr.op}")

        elif isinstance(expr, BinaryOp):
            left_str, dz_l = self.emit_expr(expr.left)
            right_str, dz_r = self.emit_expr(expr.right)
            if expr.op == "+":
                return f"new(big.Rat).Add({left_str}, {right_str})", f"({dz_l} || {dz_r})"
            elif expr.op == "-":
                return f"new(big.Rat).Sub({left_str}, {right_str})", f"({dz_l} || {dz_r})"
            elif expr.op == "*":
                return f"new(big.Rat).Mul({left_str}, {right_str})", f"({dz_l} || {dz_r})"
            elif expr.op == "/":
                # Safe division check
                return f"ratQuoSafe({left_str}, {right_str})", f"({dz_l} || {dz_r} || {right_str}.Num().Sign() == 0)"
            raise ValueError(f"Unknown binary operator: {expr.op}")

        raise TypeError(f"Unknown expression type: {type(expr)}")

    def emit_condition(self, cond: Condition) -> str:
        if isinstance(cond, Comparison):
            left_str, _ = self.emit_expr(cond.left)
            right_str, _ = self.emit_expr(cond.right)
            if cond.op == "=":
                return f"{left_str}.Cmp({right_str}) == 0"
            elif cond.op == "!=":
                return f"{left_str}.Cmp({right_str}) != 0"
            elif cond.op == ">":
                return f"{left_str}.Cmp({right_str}) > 0"
            elif cond.op == "<":
                return f"{left_str}.Cmp({right_str}) < 0"
            elif cond.op == ">=":
                return f"{left_str}.Cmp({right_str}) >= 0"
            elif cond.op == "<=":
                return f"{left_str}.Cmp({right_str}) <= 0"
            raise ValueError(f"Unknown comparison operator: {cond.op}")
        raise TypeError(f"Unknown condition type: {type(cond)}")

    def emit_stmts(self, stmts: List[Stmt]) -> List[str]:
        lines = []
        for stmt in stmts:
            lines.extend(self.emit_stmt(stmt))
        return lines

    def emit_stmt(self, stmt: Stmt) -> List[str]:
        ind = self.indent()
        lines = []

        if isinstance(stmt, StopRunStmt):
            lines.append(f"{ind}goto writeOutput")
            return lines

        elif isinstance(stmt, MoveStmt):
            target = stmt.target.upper()
            pic = self.fields[target]
            go_var = sanitize_ident(target)
            expr_str, dz = self.emit_expr(stmt.source)
            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tval := {expr_str}")
            lines.append(f"{ind}\tstored, _ := cobolStore(val, {dz}, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, false)")
            lines.append(f"{ind}\t{go_var} = stored")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, ComputeStmt):
            target = stmt.target.upper()
            pic = self.fields[target]
            go_var = sanitize_ident(target)
            expr_str, dz = self.emit_expr(stmt.expr)
            rounded = stmt.rounded
            if self.buggy_variant == "truncate_instead_of_round":
                rounded = False

            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0
            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tval := {expr_str}")
            if has_se:
                lines.append(f"{ind}\tstored, isOverflow := cobolStore(val, {dz}, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif isOverflow {{")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}} else {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.not_on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}}")
            else:
                lines.append(f"{ind}\tstored, _ := cobolStore(val, {dz}, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif !{dz} {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                lines.append(f"{ind}\t}}")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, AddStmt):
            target = (stmt.giving if stmt.giving else stmt.to_target).upper()
            pic = self.fields[target]
            go_var = sanitize_ident(target)
            rounded = stmt.rounded
            if self.buggy_variant == "truncate_instead_of_round":
                rounded = False

            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0

            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tsum := new(big.Rat)")
            for op in stmt.operands:
                e_str, _ = self.emit_expr(op)
                lines.append(f"{ind}\tsum.Add(sum, {e_str})")
            to_str, _ = self.emit_expr(Var(name=stmt.to_target))
            lines.append(f"{ind}\tsum.Add(sum, {to_str})")

            if has_se:
                lines.append(f"{ind}\tstored, isOverflow := cobolStore(sum, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif isOverflow {{")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}} else {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.not_on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}}")
            else:
                lines.append(f"{ind}\tstored, _ := cobolStore(sum, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\t{go_var} = stored")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, SubtractStmt):
            target = (stmt.giving if stmt.giving else (stmt.from_target.name if isinstance(stmt.from_target, Var) else "")).upper()
            pic = self.fields[target]
            go_var = sanitize_ident(target)
            rounded = stmt.rounded
            if self.buggy_variant == "truncate_instead_of_round":
                rounded = False

            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0

            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tsubtrahends := new(big.Rat)")
            for op in stmt.operands:
                e_str, _ = self.emit_expr(op)
                lines.append(f"{ind}\tsubtrahends.Add(subtrahends, {e_str})")
            from_str, _ = self.emit_expr(stmt.from_target)
            lines.append(f"{ind}\tdiff := new(big.Rat).Sub({from_str}, subtrahends)")

            if has_se:
                lines.append(f"{ind}\tstored, isOverflow := cobolStore(diff, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif isOverflow {{")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}} else {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.not_on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}}")
            else:
                lines.append(f"{ind}\tstored, _ := cobolStore(diff, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\t{go_var} = stored")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, MultiplyStmt):
            target = (stmt.giving if stmt.giving else (stmt.op2.name if isinstance(stmt.op2, Var) else "")).upper()
            pic = self.fields[target]
            go_var = sanitize_ident(target)
            rounded = stmt.rounded
            if self.buggy_variant == "truncate_instead_of_round":
                rounded = False

            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0
            v1_str, _ = self.emit_expr(stmt.op1)
            v2_str, _ = self.emit_expr(stmt.op2)

            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tprod := new(big.Rat).Mul({v1_str}, {v2_str})")
            if has_se:
                lines.append(f"{ind}\tstored, isOverflow := cobolStore(prod, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif isOverflow {{")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}} else {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.not_on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}}")
            else:
                lines.append(f"{ind}\tstored, _ := cobolStore(prod, false, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\t{go_var} = stored")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, DivideStmt):
            has_se = len(stmt.on_size_error) > 0 or len(stmt.not_on_size_error) > 0
            rounded = stmt.rounded
            if self.buggy_variant == "truncate_instead_of_round":
                rounded = False

            if stmt.is_by:
                target = stmt.giving.upper()
                v1_str, _ = self.emit_expr(stmt.op1)
                v2_str, _ = self.emit_expr(stmt.op2)
                quot_str = f"ratQuoSafe({v1_str}, {v2_str})"
                dz_check = f"{v2_str}.Num().Sign() == 0"
            else:
                target = (stmt.giving if stmt.giving else (stmt.op2.name if isinstance(stmt.op2, Var) else "")).upper()
                v1_str, _ = self.emit_expr(stmt.op1)
                v2_str, _ = self.emit_expr(stmt.op2)
                quot_str = f"ratQuoSafe({v2_str}, {v1_str})"
                dz_check = f"{v1_str}.Num().Sign() == 0"

            pic = self.fields[target]
            go_var = sanitize_ident(target)

            lines.append(f"{ind}{{")
            lines.append(f"{ind}\tdz := {dz_check}")
            lines.append(f"{ind}\tquot := {quot_str}")
            if has_se:
                lines.append(f"{ind}\tstored, isOverflow := cobolStore(quot, dz, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif isOverflow {{")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}} else {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                self.indent_level += 2
                lines.extend(self.emit_stmts(stmt.not_on_size_error))
                self.indent_level -= 2
                lines.append(f"{ind}\t}}")
            else:
                lines.append(f"{ind}\tstored, _ := cobolStore(quot, dz, {pic.total_digits}, {pic.scale}, {str(pic.signed).lower()}, {str(rounded).lower()})")
                lines.append(f"{ind}\tif !dz {{")
                lines.append(f"{ind}\t\t{go_var} = stored")
                lines.append(f"{ind}\t}}")
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, IfStmt):
            cond_str = self.emit_condition(stmt.cond)
            lines.append(f"{ind}if {cond_str} {{")
            self.indent_level += 1
            lines.extend(self.emit_stmts(stmt.then_branch))
            self.indent_level -= 1
            if stmt.else_branch:
                lines.append(f"{ind}}} else {{")
                self.indent_level += 1
                lines.extend(self.emit_stmts(stmt.else_branch))
                self.indent_level -= 1
            lines.append(f"{ind}}}")
            return lines

        elif isinstance(stmt, DisplayStmt):
            line_parts = []
            for item in stmt.items:
                if isinstance(item, Var):
                    var_name = item.name.upper()
                    pic = self.fields[var_name]
                    go_var = sanitize_ident(var_name)
                    line_parts.append(f"formatField({go_var}, {pic.scale})")
                elif isinstance(item, Literal):
                    line_parts.append(f'"{item.value_str}"')
                elif isinstance(item, str):
                    clean = item.replace('"', '\\"')
                    line_parts.append(f'"{clean}"')
                else:
                    expr_str, _ = self.emit_expr(item)
                    line_parts.append(f"{expr_str}.RatString()")
            joined = " + ".join(line_parts) if line_parts else '""'
            lines.append(f"{ind}displayOutput = append(displayOutput, {joined})")
            return lines

        else:
            raise NotImplementedError(f"Go emission not implemented for {type(stmt)}")

    def emit(self) -> str:
        body_lines = self.emit_stmts(self.program.body)
        body_code = "\n".join(body_lines)

        field_decls = []
        field_init_lines = []
        field_export_lines = []
        raw_export_lines = []

        for name, pic in self.fields.items():
            go_var = sanitize_ident(name)
            field_decls.append(f"\tvar {go_var} int64")
            field_init_lines.append(f'\tif val, ok := inputRaw["{name}"]; ok {{ {go_var} = parseScaled(val, {pic.scale}) }}')
            field_export_lines.append(f'\toutputFields["{name}"] = formatField({go_var}, {pic.scale})')
            raw_export_lines.append(f'\toutputRaw["{name}"] = {go_var}')

        decls_code = "\n".join(field_decls)
        inits_code = "\n".join(field_init_lines)
        exports_code = "\n".join(field_export_lines)
        raw_exports_code = "\n".join(raw_export_lines)

        go_source = f"""// Code generated by cobol2go-poc transpiler. DO NOT EDIT.
// Target: Go 1.21+
// Arithmetic: Scaled integer & math/big arbitrary precision rationals.
// Zero floating-point operations.

package main

import (
\t"encoding/json"
\t"flag"
\t"fmt"
\t"math/big"
\t"os"
\t"strings"
)

func ratFromField(val int64, scale int) *big.Rat {{
\tscaleFactor := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(scale)), nil)
\treturn new(big.Rat).SetFrac(big.NewInt(val), scaleFactor)
}}

func ratFromLiteral(scaledVal int64, scale int) *big.Rat {{
\tscaleFactor := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(scale)), nil)
\treturn new(big.Rat).SetFrac(big.NewInt(scaledVal), scaleFactor)
}}

func ratQuoSafe(a, b *big.Rat) *big.Rat {{
\tif b.Num().Sign() == 0 {{
\t\treturn new(big.Rat)
\t}}
\treturn new(big.Rat).Quo(a, b)
}}

func cobolStore(val *big.Rat, divZero bool, totalDigits int, scale int, signed bool, rounded bool) (int64, bool) {{
\tif divZero || val == nil {{
\t\treturn 0, true
\t}}
\tscaleFactor := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(scale)), nil)
\tscaledNum := new(big.Int).Mul(val.Num(), scaleFactor)
\tden := val.Denom()

\tsign := scaledNum.Sign()
\tabsNum := new(big.Int).Abs(scaledNum)

\tq := new(big.Int)
\trem := new(big.Int)
\tq.QuoRem(absNum, den, rem)

\tif rounded {{
\t\ttwoRem := new(big.Int).Mul(rem, big.NewInt(2))
\t\tif twoRem.Cmp(den) >= 0 {{
\t\t\tq.Add(q, big.NewInt(1))
\t\t}}
\t}}

\tlimit := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(totalDigits)), nil)
\tisOverflow := q.Cmp(limit) >= 0

\tvar storedVal big.Int
\tif isOverflow {{
\t\tstoredVal.Mod(q, limit)
\t\tif signed && sign < 0 {{
\t\t\tstoredVal.Neg(&storedVal)
\t\t}}
\t}} else {{
\t\tif !signed && sign < 0 {{
\t\t\tstoredVal.Set(q)
\t\t}} else if sign < 0 {{
\t\t\tstoredVal.Neg(q)
\t\t}} else {{
\t\t\tstoredVal.Set(q)
\t\t}}
\t}}

\treturn storedVal.Int64(), isOverflow
}}

func parseScaled(raw interface{{}}, scale int) int64 {{
\ts := fmt.Sprintf("%v", raw)
\ts = strings.TrimSpace(s)
\tsign := int64(1)
\tif strings.HasPrefix(s, "-") {{
\t\tsign = -1
\t\ts = strings.TrimPrefix(s, "-")
\t}} else if strings.HasPrefix(s, "+") {{
\t\ts = strings.TrimPrefix(s, "+")
\t}}

\tscaleFactor := int64(1)
\tfor i := 0; i < scale; i++ {{
\t\tscaleFactor *= 10
\t}}

\tif strings.Contains(s, ".") {{
\t\tparts := strings.SplitN(s, ".", 2)
\t\tintPart := int64(0)
\t\tfmt.Sscanf(parts[0], "%d", &intPart)
\t\tfracStr := parts[1]
\t\tif len(fracStr) < scale {{
\t\t\tfracStr = fracStr + strings.Repeat("0", scale-len(fracStr))
\t\t}} else {{
\t\t\tfracStr = fracStr[:scale]
\t\t}}
\t\tfracPart := int64(0)
\t\tfmt.Sscanf(fracStr, "%d", &fracPart)
\t\treturn sign * (intPart*scaleFactor + fracPart)
\t}}

\tintPart := int64(0)
\tfmt.Sscanf(s, "%d", &intPart)
\treturn sign * (intPart * scaleFactor)
}}

func formatField(val int64, scale int) string {{
\tsign := ""
\tif val < 0 {{
\t\tsign = "-"
\t\tval = -val
\t}}
\tif scale == 0 {{
\t\treturn fmt.Sprintf("%s%d", sign, val)
\t}}
\tscaleFactor := int64(1)
\tfor i := 0; i < scale; i++ {{
\t\tscaleFactor *= 10
\t}}
\tintPart := val / scaleFactor
\tfracPart := val % scaleFactor
\tfmtStr := fmt.Sprintf("%%s%%d.%%0%dd", scale)
\treturn fmt.Sprintf(fmtStr, sign, intPart, fracPart)
}}

func executeCase(inputRaw map[string]interface{{}}) map[string]interface{{}} {{
{decls_code}
\tdisplayOutput := make([]string, 0)

{inits_code}

{body_code}

writeOutput:
\toutputFields := make(map[string]string)
\toutputRaw := make(map[string]int64)

{exports_code}
{raw_exports_code}

\treturn map[string]interface{{}}{{
\t\t"fields":  outputFields,
\t\t"raw":     outputRaw,
\t\t"display": displayOutput,
\t}}
}}

func main() {{
\tinputPath := flag.String("input", "", "Path to single input JSON file")
\toutputPath := flag.String("output", "", "Path to single output JSON file")
\tbatchInputPath := flag.String("batch-input", "", "Path to batch input JSON file (array of inputs)")
\tbatchOutputPath := flag.String("batch-output", "", "Path to batch output JSON file")
\tflag.Parse()

\tif *batchInputPath != "" {{
\t\tdata, err := os.ReadFile(*batchInputPath)
\t\tif err != nil {{
\t\t\tpanic(err)
\t\t}}
\t\tvar cases []map[string]interface{{}}
\t\tif err := json.Unmarshal(data, &cases); err != nil {{
\t\t\tpanic(err)
\t\t}}
\t\tresults := make([]map[string]interface{{}}, 0, len(cases))
\t\tfor _, c := range cases {{
\t\t\tresults = append(results, executeCase(c))
\t\t}}
\t\toutData, err := json.MarshalIndent(results, "", "  ")
\t\tif err != nil {{
\t\t\tpanic(err)
\t\t}}
\t\tif *batchOutputPath != "" {{
\t\t\t_ = os.WriteFile(*batchOutputPath, outData, 0644)
\t\t}} else {{
\t\t\tfmt.Println(string(outData))
\t\t}}
\t\treturn
\t}}

\tinputRaw := make(map[string]interface{{}})
\tif *inputPath != "" {{
\t\tdata, err := os.ReadFile(*inputPath)
\t\tif err == nil {{
\t\t\tvar fullMap map[string]interface{{}}
\t\t\tif json.Unmarshal(data, &fullMap) == nil {{
\t\t\t\tif fMap, ok := fullMap["fields"].(map[string]interface{{}}); ok {{
\t\t\t\t\tinputRaw = fMap
\t\t\t\t}} else {{
\t\t\t\t\tinputRaw = fullMap
\t\t\t\t}}
\t\t\t}}
\t\t}}
\t}}

\tresult := executeCase(inputRaw)
\tjsonData, err := json.MarshalIndent(result, "", "  ")
\tif err != nil {{
\t\tpanic(err)
\t}}

\tif *outputPath != "" {{
\t\t_ = os.WriteFile(*outputPath, jsonData, 0644)
\t}} else {{
\t\tfmt.Println(string(jsonData))
\t}}
}}
"""
        return go_source


def emit_go(program: Program, buggy_variant: Optional[str] = None) -> str:
    emitter = GoEmitter(program, buggy_variant=buggy_variant)
    return emitter.emit()
