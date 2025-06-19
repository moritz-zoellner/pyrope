import fractions
import math
import random
import sympy

from pyrope.core import Exercise
from pyrope.nodes import (
    Equation, Expression, Natural, Integer, Problem, Rational, Set
)

class Einstein(Exercise):

    def problem(self):
        return Problem(
            """
            Einstein's most famous formula, relating Energy $E$ and mass $m$
            via the speed of light $c$, reads $E=$<<RHS>>.
            """,
            RHS=Expression(symbols='m,c')
        )

    def the_solution(self):
        return sympy.parse_expr('m * c**2')