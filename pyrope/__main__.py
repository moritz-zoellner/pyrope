
import os
import subprocess
import sys
import importlib
import unittest
from uuid import uuid4

import nbformat

from pyrope import examples, templates, ExercisePool, ExerciseRunner
from pyrope.core import CLIParser
from pyrope.frontends import ConsoleFrontend, WebFrontend


parser = CLIParser(prog='python3 -m pyrope')
args = parser.parse_args()

if args.subcommand == 'serve':
    webdir = args.webdir

    if webdir and not os.path.isdir(webdir):
        answer = input(f"Directory '{webdir}' not found. Continue using default template? (y/n): ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            sys.exit(1)
        else:
            webdir = None 

    if webdir and not os.path.isfile(os.path.join(webdir, "index.html")):
        answer = input(f"No 'index.html' found in '{webdir}'. Continue using default template? (y/n): ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            sys.exit(1)
        else:
            webdir = None
            
    if webdir and not os.path.isdir(os.path.join(webdir, "static")):
        answer = input(f"No 'static/' folder found in '{webdir}'. Continue using default template? (y/n): ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            sys.exit(1)
        else:
            webdir = None


    pool = ExercisePool()

    if not args.filepath:
        pool.add_exercises_from_module(examples)
        subpool = ExercisePool()
        subpool.add_exercises_from_module(templates)
        pool.append(subpool)
    else:
        dirname, filename = os.path.split(args.filepath)
        modulename, ext = os.path.splitext(filename)
        if ext != '.py':
            raise TypeError(f'{args.filepath} is not a Python file.')
        sys.path.insert(0, dirname)
        
        importlib.invalidate_caches()
        if modulename in sys.modules:
            module = sys.modules[modulename]
            importlib.reload(module)
        else:
            module = importlib.import_module(modulename)

        pools = [
            value for value in vars(module).values()
            if isinstance(value, ExercisePool)
        ]

        if not pools:
            raise ValueError(f'No ExercisePool found in {args.filepath}')
        if len(pools) > 1:
            raise ValueError(f'Multiple ExercisePools found in {args.filepath}')
        pool = pools[0]

    print(f"Running web frontend.")
    frontend = WebFrontend(pool,web_dir=webdir)
    frontend.run()
    print(f"Web frontend shut down.")

    sys.exit(0) 

pool = ExercisePool()

if not args.filepaths:
    pool.add_exercises_from_module(examples)
else:
    for path in args.filepaths:
        pool.add_exercises_from_file(path)

if args.subcommand == 'run':

    if args.frontend == 'console':
        for exercise in pool:
            runner = ExerciseRunner(exercise, debug=args.debug)
            frontend = ConsoleFrontend()
            runner.set_frontend(frontend)
            frontend.set_runner(runner)
            runner.run()
            pexercise = runner.pexercise
            print('trivial input:', pexercise.trivial_input)
            print('dummy input:', pexercise.dummy_input)
            print('the solution:', pexercise.the_solution)
            print('a solution:', pexercise.a_solution)
            print('solution:', pexercise.solution)
            print('answers:', pexercise.answers)
            print('scores:', {
                ifield: '{}/{}'.format(
                    pexercise.scores[ifield], pexercise.max_scores[ifield]
                )
                for ifield in pexercise.ifields
            })
            print(
                f'total score: '
                f'{pexercise.total_score}/{pexercise.max_total_score}'
            )
            print('score weights:', pexercise.score_weights)
            print('correct:', pexercise.correct)

    if args.frontend == 'jupyter':
        nb = nbformat.v4.new_notebook()
        filename = str(uuid4())
        file = f'{filename}.ipynb'
        try:
            assert os.path.isdir(args.path)
        except AssertionError:
            raise NotADirectoryError(
                f'{args.path} does not exist or is not a directory.'
            )
        else:
            file = os.path.join(args.path, file)
        code = (
            'import pyrope\n\n'
            f'%pyrope run {" ".join(args.filepaths)}'
            f'{" --debug" if args.debug else ""}'
        )
        nb['cells'] = [nbformat.v4.new_code_cell(code)]
        nb.metadata['pyrope'] = {'autoexecute': True}
        nb = nbformat.validator.normalize(nb)[1]
        with open(file, 'w') as f:
            nbformat.write(nb, f)

        jupyter_server = subprocess.Popen(['jupyter', 'notebook', file])
        try:
            jupyter_server.wait()
        except KeyboardInterrupt:
            pass

        # Cleanup
        while True:
            try:
                try:
                    os.remove(file)
                except FileNotFoundError:
                    pass
                checkpoint_path = os.path.join(
                    args.path,
                    '.ipynb_checkpoints'
                )
                try:

                    checkpoint_file = os.path.join(
                        checkpoint_path,
                        f'{filename}-checkpoint.ipynb'
                    )
                    os.remove(checkpoint_file)
                except FileNotFoundError:
                    pass
                if (
                    os.path.isdir(checkpoint_path) and
                    not os.listdir(checkpoint_path)
                ):
                    os.rmdir(checkpoint_path)
                break
            except KeyboardInterrupt:
                print('Please wait for cleanup.')
                
        
if args.subcommand == 'test':
    test_cases = [
        test_case
        for exercise in pool
        for test_case in exercise.test_cases()
    ]
    suite = unittest.TestSuite(test_cases)
    runner = unittest.TextTestRunner()
    test_result = runner.run(suite)
    if not test_result.wasSuccessful():
        sys.exit(1)
