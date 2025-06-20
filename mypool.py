from pyrope.core import ExercisePool

from mytask import Einstein
from pyrope import examples

from pyrope.templates import IntegerDivision, QuadraticEquation

pool = ExercisePool([
    Einstein(),
    ExercisePool([
        IntegerDivision(),
        QuadraticEquation(),
        examples.Factorisation(),
        ExercisePool([
            examples.FreeLunch(),
            examples.FortyTwo(),
        ])
    ]),
    examples.Factor()
])

