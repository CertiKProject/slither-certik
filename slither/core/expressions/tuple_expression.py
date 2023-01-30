from typing import List

from slither.core.expressions.expression import Expression


class TupleExpression(Expression):
    def __init__(self, expressions: List[Expression], is_inline_array: bool = False) -> None:
        assert all(isinstance(x, Expression) for x in expressions if x)
        super().__init__()
        self._expressions = expressions
        self._is_inline_array = is_inline_array

    @property
    def expressions(self) -> List[Expression]:
        return self._expressions

    @property
    def is_inline_array(self) -> bool:
        return self._is_inline_array

    def __str__(self) -> str:
        expressions_str = [str(e) for e in self.expressions]
        l_bracket, r_bracket = ("[","]") if self._is_inline_array else ("(",")")
        return l_bracket + ",".join(expressions_str) + r_bracket
