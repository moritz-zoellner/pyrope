
import abc
import argparse
import collections
from datetime import datetime
from functools import cached_property
from hashlib import sha3_256
import importlib
import inspect
import io
import itertools
import json
import logging
import os
import pathlib
import random
import sys
import unittest

from IPython import get_ipython
import numpy
from sqlalchemy.sql import select

from pyrope import config, frontends, tests
from pyrope.config import process_total_score
from pyrope.database import (
    Exercise as DBExercise, Result, Session as DBSession, User
)
from pyrope.errors import IllPosedError
from pyrope.messages import (
    ChangeWidgetAttribute, CreateWidget, ExerciseAttribute, RenderTemplate,
    Submit, WaitingForSubmission
)


float_types = (bool, int, float, numpy.bool_, numpy.integer, numpy.floating)


for name, log_config in config.logging.items():
    logger = logging.getLogger(name)
    logger.setLevel(log_config['level'])
    log_dir = pathlib.Path(log_config['filename']).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_config['filename'])
    formatter = logging.Formatter(
        fmt=log_config['fmt'], datefmt=log_config['datefmt']
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


history_log = logging.getLogger('history')


class Exercise(abc.ABC):

    # All possible metadata attributes.
    title: str = None
    subtitle: str = None
    author: str = None
    language: str = None
    license: str = None
    URL: str = None
    pyrope_versions: str | tuple = None
    origin: str = None
    discipline: str = None
    area: str = None
    topics: str | tuple = None
    keywords: str | tuple = None
    taxonomy: str | tuple = None

    __taxonomy_levels__ = (
        'knowledge',
        'comprehension',
        'application',
        'analysis',
        'synthesis',
        'evaluation',
    )

    def __init_subclass__(cls):
        old_init = cls.__init__

        def new_init(self, **kwargs):
            self.difficulty = None

            min_difficulty = kwargs.get('min_difficulty', None)
            if min_difficulty is not None:
                if not (
                    isinstance(min_difficulty, float_types) and
                    0.0 <= float(min_difficulty) <= 1.0
                ):
                    raise ValueError(
                        f"'min_difficulty' has to be an instance of "
                        f"{float_types} and has to be a number in [0, 1], "
                        f"got {min_difficulty}."
                    )
                min_difficulty = float(min_difficulty)
            self.min_difficulty = min_difficulty

            max_difficulty = kwargs.get('max_difficulty', None)
            if max_difficulty is not None:
                if not (
                    isinstance(max_difficulty, float_types) and
                    0.0 <= float(max_difficulty) <= 1.0
                ):
                    raise ValueError(
                        f"'max_difficulty' has to be an instance of "
                        f"{float_types} and has to be a number in [0, 1], "
                        f"got {max_difficulty}."
                    )
                max_difficulty = float(max_difficulty)
            self.max_difficulty = max_difficulty

            weights = kwargs.get('weights', 1)
            if not isinstance(weights, float_types + (dict,)):
                raise ValueError(
                    f"'weights' has to be an instance of "
                    f"{float_types + (dict,)}, got {type(weights)}."
                )
            if isinstance(weights, dict):
                for key, value in weights.items():
                    if not isinstance(key, str):
                        raise ValueError(
                            f"All keys of 'weights' have to be strings, "
                            f"got {type(key)}."
                        )
                    if not isinstance(value, float_types):
                        raise ValueError(
                            f"All values of 'weights' have to be an instance "
                            f"of {float_types}, got {type(value)}."
                        )
                    weights[key] = float(value)
            else:
                weights = float(weights)
            self.weights = weights

            kwargs = {
                key: kwargs[key] for key in kwargs
                if key not in ('weights', 'min_difficulty', 'max_difficulty')
            }
            old_init(self, **kwargs)

        cls.__init__ = new_init

    @cached_property
    def _source(self):
        try:
            return inspect.getsource(self.__class__)
        except OSError:
            return None

    @cached_property
    def source(self):
        classes = [
            cls()._source for cls in self.__class__.mro()[::-1]
            if issubclass(cls, Exercise) and cls != Exercise
        ]
        if None in classes:
            return None
        return '\n\n'.join(classes)

    def run(self, debug=False, difficulty=None, global_parameters=None, callback=None):
        if difficulty is not None:
            if not (
                isinstance(difficulty, float_types) and
                0.0 <= float(difficulty) <= 1.0
            ):
                raise ValueError(
                    f"'difficulty' has to be an instance of {float_types} "
                    f"and has to be a number in [0, 1], got {difficulty}."
                )
            difficulty = float(difficulty)
        self.difficulty = difficulty
        runner = ExerciseRunner(
            self, debug=debug, global_parameters=global_parameters,
            callback=callback
        )
        if get_ipython() is not None:
            frontend = frontends.JupyterFrontend()
        else:
            frontend = frontends.ConsoleFrontend()
        runner.set_frontend(frontend)
        frontend.set_runner(runner)
        runner.run()

    def preamble(self):
        return ''

    def parameters(self):
        return {}

    @abc.abstractmethod
    def problem(self):
        ...

    def the_solution(self):
        return None

    def a_solution(self):
        return None

    def hints(self):
        return []

    def scores(self):
        return None

    def feedback(self):
        return ''

    def test_cases(self):
        test_loader = unittest.TestLoader()
        method_names = test_loader.getTestCaseNames(tests.TestExercise)
        for method_name in method_names:
            yield tests.TestExercise(self, method_name)
        pexercise = ParametrizedExercise(self)
        yield from pexercise.test_cases()

    def test(self, runner=None, suppress_output=False):
        if runner is None:
            stream = io.StringIO() if suppress_output else None
            runner = unittest.TextTestRunner(stream=stream)
        suite = unittest.TestSuite(self.test_cases())
        return runner.run(suite).wasSuccessful()


class ParametrizedExercise:

    def __init__(self, exercise, global_parameters=None):
        self.exercise = exercise
        self.global_parameters = global_parameters or {}
        if 'min_difficulty' not in self.global_parameters:
            self.global_parameters['min_difficulty'] = 0.0
        if 'max_difficulty' not in self.global_parameters:
            self.global_parameters['max_difficulty'] = 1.0
        if 'user_name' not in self.global_parameters:
            self.global_parameters['user_name'] = 'John Doe'
        self._total_score = None
        self._max_total_score = None
        self.user_name = None
        self.started_at = None
        self.submitted_at = None

    @staticmethod
    def apply(func, d):
        signature = inspect.signature(func)
        kwargs = {}
        for par in signature.parameters.values():
            if par.name in d:
                kwargs[par.name] = d[par.name]
            elif par.default is inspect.Parameter.empty:
                raise IllPosedError(f'Missing parameter: {par.name}.')
        return func(**kwargs)

    def ifield_defaults(self, func):
        signature = inspect.signature(func)
        defaults = {}
        for par in signature.parameters.values():
            if (
                par.name in self.ifields and
                par.default is not inspect.Parameter.empty
            ):
                defaults[par.name] = par.default
        return defaults

    @cached_property
    def id(self):
        if self.source is None:
            return None
        return sha3_256(self.source.encode()).hexdigest()

    @cached_property
    def source(self):
        return self.exercise.source

    @cached_property
    def metadata(self):
        metadata = {}
        for name, annotation in Exercise.__annotations__.items():
            value = getattr(self.exercise.__class__, name)
            if issubclass(tuple, annotation) and isinstance(value, str):
                value = (value,)
            metadata[name] = value
        return metadata

    @cached_property
    def parameters(self):
        difficulty = self.exercise.difficulty
        if difficulty is None:
            min_ = self.exercise.min_difficulty
            max_ = self.exercise.max_difficulty
            if min_ is None:
                min_ = self.global_parameters['min_difficulty']
            if max_ is None:
                max_ = self.global_parameters['max_difficulty']
            difficulty = random.uniform(min_, max_)
        kwargs = self.global_parameters | {'difficulty': difficulty}
        pars = self.apply(self.exercise.parameters, kwargs)
        if pars is None:
            pars = {}
        return pars

    @cached_property
    def model(self):
        model = self.apply(self.exercise.problem, self.parameters)
        for ofield in model.ofields:
            if ofield not in self.parameters:
                raise IllPosedError(
                    f"No parameter for output field '{ofield}'."
                )
        return model

    @cached_property
    def template(self):
        return str(self.model)

    @cached_property
    def preamble(self):
        preamble = self.exercise.preamble
        if callable(preamble):
            preamble = self.apply(preamble, self.parameters)
        return preamble if preamble is not None else ''

    @cached_property
    def ifields(self):
        return self.model.ifields

    @cached_property
    def widgets(self):
        return self.model.widgets

    @cached_property
    def the_solution(self):
        explicit = self.apply(self.exercise.the_solution, self.parameters)
        if explicit is None:
            explicit = {}
        elif not isinstance(explicit, dict):
            if len(self.ifields) != 1:
                raise IllPosedError(
                    'Unless there is only a single input field, '
                    'the solution must be a dictionary.'
                )
            names = list(self.ifields.keys())
            explicit = {names[0]: explicit}

        # implicit solutions from underscore naming convention
        implicit = {
            ifield: self.parameters[ifield[:-1]]
            for ifield in self.ifields
            if ifield.endswith('_')
            if ifield[:-1] in self.parameters
            if ifield not in explicit
        }

        solution = explicit | implicit

        self.model.the_solution = solution
        return {
            name: ifield.the_solution
            for name, ifield in self.model.ifields.items()
            if name in solution
        }

    @cached_property
    def a_solution(self):
        solution = self.apply(self.exercise.a_solution, self.parameters)
        if solution is None:
            solution = {}
        elif not isinstance(solution, dict):
            if len(self.ifields) != 1:
                raise IllPosedError(
                    'Unless there is only a single input field, '
                    'a solution must be a dictionary.'
                )
            names = list(self.ifields.keys())
            solution = {names[0]: solution}

        self.model.a_solution = solution
        return {
            name: ifield.a_solution
            for name, ifield in self.model.ifields.items()
            if name in solution
        }

    @property
    def solution(self):
        # trigger solutions via getter
        names = set(self.the_solution.keys()) | set(self.a_solution.keys())
        return {
            name: ifield.solution
            for name, ifield in self.model.ifields.items()
            if name in names
        }

    @cached_property
    def hints(self):
        hints = self.apply(self.exercise.hints, self.parameters)
        if isinstance(hints, str):
            hints = (hints,)
        if isinstance(hints, dict):
            if len(hints) == 0:
                return ()
            for name in self.ifields.keys():
                ifield_hints = hints.get(name, ())
                if isinstance(ifield_hints, str):
                    ifield_hints = (ifield_hints,)
                hints[name] = tuple(ifield_hints)
            if len(self.ifields) == 1:
                return list(hints.values())[0]
            return hints
        return tuple(hints)

    @cached_property
    def trivial_input(self):
        return {
            name: ifield.dtype.trivial_value()
            for name, ifield in self.ifields.items()
        }

    @cached_property
    def dummy_input(self):
        return {
            name: ifield.dtype.dummy_value()
            for name, ifield in self.ifields.items()
        }

    @property
    def answers(self):
        return self.model.answers

    @answers.setter
    def answers(self, answers):
        self.model.value = answers

    @cached_property
    def score_weights(self):
        weights = self.exercise.weights
        if isinstance(weights, dict):
            for key in weights:
                if key not in self.ifields:
                    raise ValueError(
                        f"All keys of 'weights' have to match an input field. "
                        f"There is no input field '{key}'."
                    )
        if isinstance(weights, float_types):
            if len(self.ifields) == 0:
                return {None: weights}
            return {name: weights for name in self.ifields}
        weights = weights.copy()
        for name in self.ifields:
            if name not in weights:
                weights[name] = 1.0
        return weights

    @cached_property
    def max_scores(self):
        solution = self.solution
        scores = self.apply(
            self.exercise.scores, self.parameters | self.dummy_input
        )

        if scores is None:
            max_scores = {
                name: float(ifield.auto_max_score) * self.score_weights[name]
                for name, ifield in self.ifields.items()
            }
            self._max_total_score = sum(max_scores.values())
            for name, ifield in self.ifields.items():
                ifield.displayed_max_score = max_scores[name]
            return max_scores

        if isinstance(scores, tuple):
            self._max_total_score = (
                float(scores[1]) * list(self.score_weights.values())[0]
            )
            if len(self.ifields) == 1:
                name = list(self.ifields.keys())[0]
                ifield = list(self.ifields.values())[0]
                ifield.displayed_max_score = self._max_total_score
                return {name: self._max_total_score}
            return {name: None for name in self.ifields}

        if isinstance(scores, float_types):
            for name in self.ifields:
                if name not in solution:
                    raise IllPosedError(
                        f"Unable to determine a maximal total score because "
                        f"there is no solution for input field '{name}'."
                    )
            max_scores = self.apply(
                self.exercise.scores, self.parameters | solution
            )
            self._max_total_score = (
                float(max_scores) * list(self.score_weights.values())[0]
            )
            if len(self.ifields) == 1:
                name = list(self.ifields.keys())[0]
                ifield = list(self.ifields.values())[0]
                ifield.displayed_max_score = self._max_total_score
                return {name: self._max_total_score}
            return {name: None for name in self.ifields}

        if isinstance(scores, dict):
            answer = {
                name: solution[name] if name in solution
                else self.dummy_input[name]
                for name in self.ifields
            }

            max_scores = self.apply(
                self.exercise.scores, self.parameters | answer
            )

            for name in self.ifields:
                # Fill up missing input field names with None.
                if name not in max_scores:
                    max_scores[name] = None
                # In case a dummy input obtained a float type score.
                elif name not in solution:
                    if isinstance(max_scores[name], float_types):
                        max_scores[name] = None

            for name, value in max_scores.items():
                if isinstance(value, float_types):
                    max_scores[name] = float(value)
                elif isinstance(value, tuple):
                    max_scores[name] = float(value[1])
                elif value is None:
                    max_scores[name] = self.ifields[name].auto_max_score
                max_scores[name] *= self.score_weights[name]
                self.ifields[name].displayed_max_score = max_scores[name]
            self._max_total_score = sum(max_scores.values())
            return max_scores

        raise IllPosedError(
            'If implemented, the score method must return a number, a pair '
            'of numbers or a dictionary with values of this type, where a '
            'number is either an int or a float.'
        )

    @property
    def scores(self):
        self.solution  # Essential for a successful auto scoring.
        output = self.apply(
            self.exercise.scores, self.parameters | self.dummy_input
        )
        # Get all non-empty answers.
        answers = {
            name: value for name, value in self.answers.items()
            if value is not None
        }
        no_scores = {name: None for name in self.ifields}

        # Joint input field scoring.
        if (
            isinstance(output, float_types + (tuple,)) and
            len(self.ifields) != 1
        ):
            # In a joint input field scoring scenario all input fields need
            # to have non-empty answers.
            if set(answers.keys()) != set(self.ifields.keys()):
                self._total_score = 0.0
                return no_scores
            score = self.apply(
                self.exercise.scores, self.parameters | answers
            )
            if isinstance(score, float_types):
                score = float(score)
            else:
                score = float(score[0])
            self._total_score = score * list(self.score_weights.values())[0]
            return no_scores

        defaults = self.ifield_defaults(self.exercise.scores)
        # Use default values if there is no answer.
        answers = defaults | answers
        # Use dummy inputs to fill up empty answers.
        fill_values = {
            name: self.dummy_input[name] for name in self.ifields
            if name not in answers
        }
        answers |= fill_values
        scores = self.apply(
            self.exercise.scores, self.parameters | answers
        )

        # Input field-wise scoring for float and tuple types if there is only
        # one input field.
        if isinstance(scores, float_types + (tuple,)):
            name = list(self.ifields.keys())[0]
            if isinstance(scores, float_types):
                scores = float(scores)
            else:
                scores = float(scores[0])
            self._total_score = scores * self.score_weights[name]
            self.ifields[name].displayed_score = self._total_score
            return {name: self._total_score}

        # Cast scores and fill up missing scores with None.
        if isinstance(scores, dict):
            for name in self.ifields:
                if name in scores and name in fill_values:
                    scores[name] = 0.0
                if name in scores:
                    if isinstance(scores[name], tuple):
                        scores[name] = scores[name][0]
                    scores[name] = float(scores[name])
                else:
                    scores[name] = None

        if scores is None:
            scores = no_scores

        for name, ifield in self.ifields.items():
            if scores[name] is None:
                scores[name] = ifield.auto_score
            scores[name] = scores[name] * self.score_weights[name]
            ifield.displayed_score = scores[name]

        self._total_score = sum(scores.values())
        return scores

    @property
    def total_score(self):
        self.scores  # trigger total score computation
        return self._total_score

    @property
    def max_total_score(self):
        self.max_scores  # trigger maximal total score computation
        return self._max_total_score

    @property
    def correct(self):
        max_scores = self.max_scores
        scores = self.scores
        correct = {}
        for name, ifield in self.ifields.items():
            if (
                ifield.correct is None and
                scores[name] is not None and
                max_scores[name] is not None
            ):
                if scores[name] == max_scores[name]:
                    correct[name] = True
                else:
                    correct[name] = False
                ifield.correct = correct[name]
            else:
                correct[name] = ifield.correct
        return correct

    @property
    def feedback(self):
        kwargs = self.parameters | self.answers
        feedback = self.apply(self.exercise.feedback, kwargs)
        return feedback if feedback is not None else ''

    @cached_property
    def input_generator(self):
        answers = [
            self.trivial_input,
            self.dummy_input,
            self.solution,
        ]
        keys = self.ifields.keys()
        factors = []
        for key in keys:
            factor = [None]
            for answer in answers:
                if answer.get(key, None) is not None:
                    factor.append(answer[key])
            factors.append(factor)

        def generator():
            for values in itertools.product(*factors):
                yield dict(zip(keys, values))

        return generator

    def test_cases(self):
        test_loader = unittest.TestLoader()
        method_names = test_loader.getTestCaseNames(
            tests.TestParametrizedExercise
        )
        for method_name in method_names:
            yield tests.TestParametrizedExercise(self, method_name)

    @property
    def summary(self):
        summary = {'name': self.exercise.__class__.__name__}
        summary |= {
            property: getattr(self, property)
            for property in config.summary_items
        }
        return summary


class ExerciseRunner:

    def __init__(self, exercise, debug=False, global_parameters=None, callback=None):
        self.debug = debug
        self.observers = []
        self.pexercise = ParametrizedExercise(exercise, global_parameters)
        user_name = self.pexercise.global_parameters['user_name']
        self.pexercise.user_name = user_name
        self.sender = exercise.__class__
        self.widget_id_mapping = {
            widget.ID: widget for widget in self.pexercise.widgets
        }
        self.callback = callback
        if self.pexercise.id is None:
            return
        with DBSession() as session:
            user = session.scalar(select(User.name).where(
                User.name == user_name
            ))
            if user is None:
                session.add(User(name=user_name))
            db_exercise = session.scalar(select(DBExercise.id).where(
                DBExercise.id == self.pexercise.id
            ))
            if db_exercise is None:
                label = self.pexercise.metadata['title']
                if label is None:
                    label = self.pexercise.exercise.__class__.__name__
                session.add(DBExercise(
                    id=self.pexercise.id, source=self.pexercise.source,
                    label=label, score_maximum=self.pexercise.max_total_score
                ))
            session.commit()
            self.user_id = session.scalar(select(User.id).where(
                User.name == user_name
            ))

    # TODO: enforce order of steps
    def run(self):
        self.notify(ExerciseAttribute(self.sender, 'debug', self.debug))
        self.notify(ExerciseAttribute(
            self.sender, 'parameters', self.pexercise.parameters
        ))
        self.notify(ExerciseAttribute(
            self.sender, 'hints', self.pexercise.hints
        ))
        self.notify(RenderTemplate(
            self.sender, 'preamble', self.pexercise.preamble
        ))
        for widget in self.pexercise.widgets:
            self.notify(CreateWidget(
                self.sender, widget.ID, widget.__class__.__name__
            ))
            widget.observe_attributes()
            self.notify(ChangeWidgetAttribute(
                repr(widget), widget.ID, 'info', widget.info
            ))
            # self.notify(ChangeWidgetAttribute(
            #     repr(widget), widget.ID, 'ifield_name', widget.ifield_name
            # ))
        self.notify(RenderTemplate(
            self.sender, 'problem', self.pexercise.template
        ))
        if self.debug:
            self.publish_solutions()
        self.notify(WaitingForSubmission(self.sender))
        self.pexercise.started_at = datetime.utcnow()

    def finish(self):
        self.pexercise.submitted_at = datetime.utcnow()
        if not self.debug:
            self.publish_solutions()
        self.notify(ExerciseAttribute(
            self.sender, 'answers', self.pexercise.answers
        ))
        self.notify(ExerciseAttribute(
            self.sender, 'max_total_score',
            process_total_score(self.pexercise.max_total_score)
        ))
        self.notify(ExerciseAttribute(
            self.sender, 'total_score',
            process_total_score(self.pexercise.total_score)
        ))
        self.pexercise.correct
        for widget in self.pexercise.model.widgets:
            widget.show_max_score = True
            widget.show_score = True
            widget.show_correct = True
        self.notify(RenderTemplate(
            self.sender, 'feedback', self.pexercise.feedback
        ))
        if self.pexercise.id is None:
            return
        with DBSession() as session:
            session.add(Result(
                exercise_id=self.pexercise.id, user_id=self.user_id,
                started_at=self.pexercise.started_at,
                submitted_at=self.pexercise.submitted_at,
                score_given=self.pexercise.total_score
            ))
            session.commit()
        if self.callback is not None:
            self.callback(
                self.pexercise.total_score,
                self.pexercise.max_total_score
            )
        history_log.info(json.dumps(self.pexercise.summary, default=str))

    def publish_solutions(self):
        self.pexercise.solution
        for widget in self.pexercise.model.widgets:
            widget.show_solution = True

    def set_frontend(self, frontend):
        self.frontend = frontend

    def register_observer(self, observer):
        self.observers.append(observer)
        for widget in self.pexercise.widgets:
            widget.register_observer(observer)

    def notify(self, msg):
        for observer in self.observers:
            observer(msg)

    def observer(self, msg):
        if isinstance(msg, ChangeWidgetAttribute):
            if msg.attribute_name == 'value':
                widget = self.widget_id_mapping[msg.widget_id]
                widget.value = msg.attribute_value
        elif isinstance(msg, Submit):
            self.finish()


class ExercisePool(collections.UserList):

    def add_exercise(self, exercise):
        self.data.append(exercise)

    def add_exercises_from_pool(self, pool):
        self.data.extend(pool)

    def add_exercises_from_module(self, module, *exercise_names):
        # Calling the '__dir__' method instead of the 'dir' built-in
        # avoids alphabetical sorting of the exercises added to the pool.
        if len(exercise_names) == 0:
            exercise_names = module.__dir__()
        for name in exercise_names:
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, Exercise):
                if not inspect.isabstract(obj):
                    self.data.append(obj())

    def add_exercises_from_file(self, filepath):
        dirname, filename = os.path.split(filepath)
        filename, exercises, *_ = *filename.split(':'), None
        if exercises is None:
            exercises = []
        else:
            exercises = [name.strip() for name in exercises.split(',')]
        modulename, ext = os.path.splitext(filename)
        if ext != '.py':
            raise TypeError(
                f'File "{filepath}" does not seem to be a python script.'
            )
        sys.path.insert(0, dirname)
        importlib.invalidate_caches()
        if modulename in sys.modules:
            module = sys.modules[modulename]
            importlib.reload(module)
        else:
            module = importlib.import_module(modulename)
        self.add_exercises_from_module(module, *exercises)


class CLIParser:

    def __init__(self, *args, default_frontend='jupyter', **kwargs):
        self._parser = argparse.ArgumentParser(*args, **kwargs)
        subparsers = self._parser.add_subparsers(
            dest='subcommand',
            required=True,
            help='subcommands'
        )

        run_parser = subparsers.add_parser(
            'run',
            help='start an interactive exercise session'
        )
        run_parser.add_argument(
            'filepaths',
            nargs='*',
            type=str,
            help='paths to python scripts with exercise definitions',
            metavar='filepath',
        )
        run_parser.add_argument(
            '--frontend',
            default=default_frontend,
            choices={'console', 'jupyter'},
            help='exercises renderer',
        )
        run_parser.add_argument(
            '--path',
            default=os.getcwd(),
            help='location to store files'
        )
        run_parser.add_argument(
            '--debug',
            default=False,
            action='store_true',
            help='enables debug mode for the frontend',
        )

        serve_parser = subparsers.add_parser(
            'serve',
            help='serve a pool of exercises in a web interface'
        )
        serve_parser.add_argument(
            'filepath',
            nargs='?',
            type=str,
            help='path to python script with definition of exercise pool'
        )
        serve_parser.add_argument(
            '--webdir',
            type=str,
            default=None,
            help='path to folder with frontend template (with ./index.html and ./static/)'
        )


        test_parser = subparsers.add_parser(
            'test',
            help='run automated unit tests on exercises'
        )
        test_parser.add_argument(
            'filepaths',
            nargs='*',
            type=str,
            help='paths to python scripts with exercise definitions',
            metavar='filepath',
        )

    def parse_args(self, args=None, namespace=None):
        return self._parser.parse_args(args=args, namespace=namespace)