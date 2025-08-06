from pyrope.core import Quiz 

from mytask import Einstein
from pyrope import examples

from pyrope.templates import IntegerDivision, QuadraticEquation

pool = Quiz(
    title='mein eigenes quiz',
    navigation='free',
    # weights={0:3,1:2},
    items = [
    Einstein(),
    Quiz(
        title = 'Unterquiz',
        navigation = 'free',
        items = [
        IntegerDivision(),
        Quiz(
            title = 'Leichte Aufgaben',
            navigation = 'free',
            # weights = { 0: 0, 1: 3},
            items = [
            examples.FreeLunch(),
            examples.FortyTwo(),
        ]),
        QuadraticEquation(),
        examples.Factorisation(),
    ]),
    examples.Factor()
],
)
'''
pool[1].weights = {
    0: 4,
    2: 2
}
'''