"""
COBOL Subset v0.1 Parser for cobol2go-poc using Lark.

Parses IDENTIFICATION, DATA, and PROCEDURE divisions with:
- PIC clauses: 9(n), 9(n)V9(m), S9(n)V9(m)
- Statements: MOVE, ADD, SUBTRACT, MULTIPLY, DIVIDE, COMPUTE, IF/ELSE, DISPLAY, STOP RUN
- Options: ROUNDED, ON SIZE ERROR, NOT ON SIZE ERROR
"""

from __future__ import annotations
import re
from typing import List, Dict, Union, Optional
from lark import Lark, Transformer, v_args, Token, Tree

from src.ir import (
    PicType, Program, Stmt, Expr, Condition,
    MoveStmt, AddStmt, SubtractStmt, MultiplyStmt, DivideStmt,
    ComputeStmt, IfStmt, DisplayStmt, StopRunStmt,
    Var, Literal, BinaryOp, UnaryOp, Comparison
)

COBOL_GRAMMAR = r"""
start: program

program: ident_div data_div proc_div

ident_div: "IDENTIFICATION" "DIVISION" "." "PROGRAM-ID" "." IDENT "."

data_div: "DATA" "DIVISION" "." "WORKING-STORAGE" "SECTION" "." data_item*

data_item: "01" IDENT "PIC" PIC_CLAUSE "."

proc_div: "PROCEDURE" "DIVISION" "." statement*

statement: (move_stmt
         | add_stmt
         | subtract_stmt
         | multiply_stmt
         | divide_stmt
         | compute_stmt
         | if_stmt
         | display_stmt
         | stop_run_stmt) [DOT_SEP]

move_stmt: "MOVE" expr "TO" IDENT

add_stmt: "ADD" expr+ "TO" IDENT ["GIVING" IDENT] [rounded] [size_error] [not_size_error] [END_ADD]

subtract_stmt: "SUBTRACT" expr+ "FROM" expr ["GIVING" IDENT] [rounded] [size_error] [not_size_error] [END_SUBTRACT]

multiply_stmt: "MULTIPLY" expr "BY" expr ["GIVING" IDENT] [rounded] [size_error] [not_size_error] [END_MULTIPLY]

divide_stmt: "DIVIDE" expr "INTO" expr ["GIVING" IDENT] [rounded] [size_error] [not_size_error] [END_DIVIDE] -> divide_into
           | "DIVIDE" expr "BY" expr "GIVING" IDENT [rounded] [size_error] [not_size_error] [END_DIVIDE] -> divide_by

compute_stmt: "COMPUTE" IDENT [rounded] "=" expr [size_error] [not_size_error] [END_COMPUTE]

rounded: "ROUNDED"

size_error: ["ON"] "SIZE" "ERROR" statement+
not_size_error: "NOT" ["ON"] "SIZE" "ERROR" statement+

if_stmt: "IF" condition ["THEN"] then_branch [else_clause] [END_IF]
then_branch: statement+
else_clause: "ELSE" statement+

display_stmt: "DISPLAY" display_item+

?display_item: expr
             | STRING -> str_lit

stop_run_stmt: "STOP" "RUN"

condition: expr comp_op expr

comp_op: "="           -> op_eq
       | "EQUAL" ["TO"] -> op_eq
       | ">"           -> op_gt
       | "GREATER" ["THAN"] -> op_gt
       | "<"           -> op_lt
       | "LESS" ["THAN"] -> op_lt
       | ">="          -> op_ge
       | "<="          -> op_le
       | "!="          -> op_ne
       | "<>"          -> op_ne
       | "NOT" "="     -> op_ne
       | "NOT" "EQUAL" ["TO"] -> op_ne

?expr: sum_expr

?sum_expr: prod_expr
         | sum_expr "+" prod_expr -> add_op
         | sum_expr "-" prod_expr -> sub_op

?prod_expr: atom_expr
          | prod_expr "*" atom_expr -> mul_op
          | prod_expr "/" atom_expr -> div_op

?atom_expr: "(" expr ")"
          | "-" atom_expr -> neg_op
          | "+" atom_expr -> pos_op
          | NUMBER        -> num_lit
          | IDENT         -> var_expr

END_ADD: "END-ADD"
END_SUBTRACT: "END-SUBTRACT"
END_MULTIPLY: "END-MULTIPLY"
END_DIVIDE: "END-DIVIDE"
END_COMPUTE: "END-COMPUTE"
END_IF: "END-IF"

PIC_CLAUSE: /[A-Z0-9\(\)]+/
IDENT.-1: /[A-Z][A-Z0-9\-]*/
NUMBER: /[0-9]+(\.[0-9]+)?/
STRING: /"([^"\\]|\\.)*"/ | /'([^'\\]|\\.)*'/
DOT_SEP: "." | ";"

%import common.WS
%ignore WS
"""


def normalize_cobol(source: str) -> str:
    """
    Preprocess COBOL source:
    - Strip comment lines (* in first column or * preceded only by whitespace)
    - Strip inline comments (*>)
    - Uppercase all characters OUTSIDE string literals
    """
    clean_lines = []
    for line in source.splitlines():
        trimmed = line.strip()
        # Full-line comment
        if trimmed.startswith("*") and not trimmed.startswith("*>"):
            continue
        # Inline comment
        if "*>" in line:
            line = line[:line.index("*>")]
        clean_lines.append(line)

    text = "\n".join(clean_lines)

    # Uppercase outside string literals
    result = []
    in_str = False
    quote_char = ''
    i = 0
    while i < len(text):
        c = text[i]
        if not in_str:
            if c in ('"', "'"):
                in_str = True
                quote_char = c
                result.append(c)
            else:
                result.append(c.upper())
        else:
            result.append(c)
            if c == quote_char:
                if i > 0 and text[i - 1] == '\\':
                    pass
                else:
                    in_str = False
        i += 1

    return "".join(result)


class CobolTransformer(Transformer):
    def __init__(self):
        super().__init__()
        self.fields: Dict[str, PicType] = {}

    def start(self, items) -> Program:
        return items[0]

    def program(self, items) -> Program:
        prog_id = items[0]
        fields = items[1]
        stmts = items[2]
        return Program(name=prog_id, fields=fields, body=stmts)

    def ident_div(self, items) -> str:
        return str(items[0])

    def data_div(self, items) -> Dict[str, PicType]:
        fields = {}
        for item in items:
            name, pic = item
            fields[name] = pic
        self.fields = fields
        return fields

    def data_item(self, items) -> tuple[str, PicType]:
        name = str(items[0])
        pic = PicType.parse(str(items[1]))
        return name, pic

    def proc_div(self, items) -> List[Stmt]:
        stmts = []
        for it in items:
            if isinstance(it, Stmt):
                stmts.append(it)
        return stmts

    def statement(self, items) -> Optional[Stmt]:
        for it in items:
            if isinstance(it, Stmt):
                return it
        return None

    def move_stmt(self, items) -> MoveStmt:
        return MoveStmt(source=items[0], target=str(items[1]))

    def add_stmt(self, items) -> AddStmt:
        operands = []
        to_target = None
        giving = None
        rounded = False
        on_size = []
        not_size = []

        idx = 0
        while idx < len(items):
            it = items[idx]
            if isinstance(it, Expr):
                operands.append(it)
                idx += 1
            else:
                break

        to_target = str(items[idx])
        idx += 1

        for it in items[idx:]:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, str) and it not in ("ROUNDED", "END-ADD"):
                giving = it
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        return AddStmt(
            operands=operands,
            to_target=to_target,
            giving=giving,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size
        )

    def subtract_stmt(self, items) -> SubtractStmt:
        operands = []
        from_target = None
        giving = None
        rounded = False
        on_size = []
        not_size = []

        expr_list = [it for it in items if isinstance(it, Expr)]
        operands = expr_list[:-1]
        from_target = expr_list[-1]

        for it in items:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, str) and it not in ("ROUNDED", "END-SUBTRACT") and not any(it == e.name for e in operands if isinstance(e, Var)) and not (isinstance(from_target, Var) and it == from_target.name):
                giving = it
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        return SubtractStmt(
            operands=operands,
            from_target=from_target,
            giving=giving,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size
        )

    def multiply_stmt(self, items) -> MultiplyStmt:
        op1 = items[0]
        op2 = items[1]
        giving = None
        rounded = False
        on_size = []
        not_size = []

        for it in items[2:]:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, str) and it not in ("ROUNDED", "END-MULTIPLY"):
                giving = it
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        return MultiplyStmt(
            op1=op1,
            op2=op2,
            giving=giving,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size
        )

    def divide_into(self, items) -> DivideStmt:
        op1 = items[0]
        op2 = items[1]
        giving = None
        rounded = False
        on_size = []
        not_size = []

        for it in items[2:]:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, str) and it not in ("ROUNDED", "END-DIVIDE"):
                giving = it
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        return DivideStmt(
            op1=op1,
            op2=op2,
            giving=giving,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size,
            is_by=False
        )

    def divide_by(self, items) -> DivideStmt:
        op1 = items[0]
        op2 = items[1]
        giving = str(items[2])
        rounded = False
        on_size = []
        not_size = []

        for it in items[3:]:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        return DivideStmt(
            op1=op1,
            op2=op2,
            giving=giving,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size,
            is_by=True
        )

    def compute_stmt(self, items) -> ComputeStmt:
        target = str(items[0])
        rounded = False
        expr = None
        on_size = []
        not_size = []

        for it in items[1:]:
            if it == "ROUNDED":
                rounded = True
            elif isinstance(it, Expr):
                expr = it
            elif isinstance(it, tuple):
                tag, stmts = it
                if tag == "SIZE_ERROR":
                    on_size = stmts
                elif tag == "NOT_SIZE_ERROR":
                    not_size = stmts

        assert expr is not None, f"COMPUTE statement missing expression: {items}"
        return ComputeStmt(
            target=target,
            expr=expr,
            rounded=rounded,
            on_size_error=on_size,
            not_on_size_error=not_size
        )

    def rounded(self, _) -> str:
        return "ROUNDED"

    def size_error(self, items) -> tuple[str, List[Stmt]]:
        stmts = [it for it in items if isinstance(it, Stmt)]
        return ("SIZE_ERROR", stmts)

    def not_size_error(self, items) -> tuple[str, List[Stmt]]:
        stmts = [it for it in items if isinstance(it, Stmt)]
        return ("NOT_SIZE_ERROR", stmts)

    def then_branch(self, items) -> List[Stmt]:
        return [it for it in items if isinstance(it, Stmt)]

    def else_clause(self, items) -> List[Stmt]:
        return [it for it in items if isinstance(it, Stmt)]

    def if_stmt(self, items) -> IfStmt:
        cond = items[0]
        then_branch = items[1]
        else_branch = items[2] if len(items) > 2 and isinstance(items[2], list) else []
        return IfStmt(cond=cond, then_branch=then_branch, else_branch=else_branch)

    def display_stmt(self, items) -> DisplayStmt:
        res = []
        for it in items:
            if isinstance(it, Expr):
                res.append(it)
            elif isinstance(it, str):
                res.append(it.strip("\"'"))
        return DisplayStmt(items=res)

    def stop_run_stmt(self, items) -> StopRunStmt:
        return StopRunStmt()

    def condition(self, items) -> Comparison:
        return Comparison(op=items[1], left=items[0], right=items[2])

    def op_eq(self, _) -> str: return "="
    def op_gt(self, _) -> str: return ">"
    def op_lt(self, _) -> str: return "<"
    def op_ge(self, _) -> str: return ">="
    def op_le(self, _) -> str: return "<="
    def op_ne(self, _) -> str: return "!="

    def add_op(self, items) -> BinaryOp:
        return BinaryOp(op="+", left=items[0], right=items[1])

    def sub_op(self, items) -> BinaryOp:
        return BinaryOp(op="-", left=items[0], right=items[1])

    def mul_op(self, items) -> BinaryOp:
        return BinaryOp(op="*", left=items[0], right=items[1])

    def div_op(self, items) -> BinaryOp:
        return BinaryOp(op="/", left=items[0], right=items[1])

    def neg_op(self, items) -> UnaryOp:
        return UnaryOp(op="-", operand=items[0])

    def pos_op(self, items) -> Expr:
        return items[0]

    def num_lit(self, items) -> Literal:
        return Literal.numeric(str(items[0]))

    def str_lit(self, items) -> str:
        return str(items[0]).strip("\"'")

    def var_expr(self, items) -> Var:
        return Var(name=str(items[0]))

    def IDENT(self, token: Token) -> str:
        return str(token)

    def PIC_CLAUSE(self, token: Token) -> str:
        return str(token)

    def NUMBER(self, token: Token) -> str:
        return str(token)

    def STRING(self, token: Token) -> str:
        return str(token)


class CobolParser:
    def __init__(self):
        self.parser = Lark(COBOL_GRAMMAR, parser='earley')

    def parse(self, source: str) -> Program:
        normalized = normalize_cobol(source)
        tree = self.parser.parse(normalized)
        transformer = CobolTransformer()
        return transformer.transform(tree)


def parse_cobol(source: str) -> Program:
    parser = CobolParser()
    return parser.parse(source)
