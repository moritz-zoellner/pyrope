import subprocess, os, shutil, threading

import nbformat

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import uvicorn
import webbrowser, time


class WebFrontend:

    def __init__(self, quiz, web_dir = None):
        self.quiz = quiz
        self.voila = None
        self.tmp_dir = 'tmp_dir'

        self.web_dir = web_dir or 'pyrope/web'
        self.app = FastAPI()
        self.api_host = "127.0.0.1"
        self.api_port = 8000
        self._setup_api()

# ------ Rest API ------

    def _setup_api(self):
        self.app.mount("/static", StaticFiles(directory = os.path.join(self.web_dir, 'static')), name="static")

    # Root-Endpunkt: Liefert einfach eine HTML-Datei
        @self.app.get("/")
        def root():
            return FileResponse(os.path.join(self.web_dir, 'index.html'))
        @self.app.get("/structure")
        def get_structure():
            return self._build_structure(self.quiz)
    
    def _start_api(self):
        uvicorn.run(self.app, host=self.api_host, port=self.api_port, log_level="info")

    def _build_structure(self, quiz):
        from pyrope.core import Exercise, Quiz

        structure = {
            "title": quiz.title,
            "navigation": quiz.navigation,
            "weights": quiz.weights,
            "items": []
        }

        count_exercises = 0
        count_quizzes = 0

        for item in quiz:
            if isinstance(item, Exercise):
                structure["items"].append(f'{count_exercises}_{item.__class__.__name__}.ipynb')
                count_exercises += 1
            elif isinstance(item, Quiz):
                sub_structure = self._build_structure(item)
                sub_structure["title"] = item.title or f'{count_quizzes}_subquiz'
                structure["items"].append(sub_structure)
                count_quizzes += 1

        return structure
        
    def _quiz_to_dict(self, quiz):
        from pyrope.core import Exercise, Quiz

        items = []
        count_exercises = 0
        count_quizzes = 0

        for item in quiz:
            if isinstance(item, Exercise):
                items.append({"type": "exercise", "name": f'{count_exercises}_{item.__class__.__name__}.ipynb'})
                count_exercises += 1
            elif isinstance(item, Quiz):
                items.append({"type": "quiz", "name": item.title or f'{count_quizzes}_subquiz'  , "items": self._quiz_to_dict(item)})
                count_quizzes += 1
        return items
    

# ------ Voila/Notebooks ------  

    def _build_notebook_dir(self, quiz, dir, path=''):
        from pyrope.core import Exercise, Quiz

        os.makedirs(dir, exist_ok=True)
        count_exercises = 0
        count_quizzes = 0

        for item in quiz:
            if isinstance(item, Exercise):

                name = item.__class__.__name__
                module = item.__class__.__module__

                nb = nbformat.v4.new_notebook()

                file_name = f'{count_exercises}_{name}'

                path_id = f"{path}/{file_name}"
                code = (
                    'import sys, os, json\n'
                    'import ipywidgets as widgets\n'
                    'from IPython.display import Javascript\n'
                    'sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..")))\n'
                    f'from {module} import {name}\n'
                    f'id = "{path_id}"\n'
                    'outputSpace=widgets.Output()\n'
                    'display(outputSpace)\n'
                    'def postResult(score, max_score):\n' \
                    '   message = {\n'
                    '    "type": "results", "id": id, "score": score, "maxScore": max_score\n'
                    '   }\n' \
                    '   js_code=f"window.parent.postMessage({json.dumps(message)},\\"*\\");"\n' #console.log({json.dumps(message)})"\n'
                    '   with outputSpace: \n'
                    '       display(Javascript(js_code))\n'
                    f'{name}().run(callback=postResult)\n '
                )
                
                nb['cells'] = [nbformat.v4.new_code_cell(code)]

                file = f'{file_name}.ipynb'
                with open(os.path.join(dir, file), 'w') as f:
                    nbformat.write(nb, f)

                count_exercises += 1

            elif isinstance(item, Quiz):
                quiz_name = item.title or f'{count_quizzes}_subquiz'
                subdir = os.path.join(dir, quiz_name)
                self._build_notebook_dir(item, subdir, f'{path}/{quiz_name}')

                count_quizzes += 1

    def _open_voila(self):
        
        settings_str = (
            "{"
            "  'headers': {"
            "    'Content-Security-Policy': \"frame-ancestors 'self' *\""
            "  }, "
            "  'disable_check_xsrf': True"
            "}"
        )
            
        process = subprocess.Popen([
            "voila",
            "tmp_dir",
            "--no-browser",
            f"--Voila.tornado_settings={settings_str}"
        ])
        return process

# ------ Put it all together ------

    def run(self):
        try:
            self._build_notebook_dir(self.quiz, self.tmp_dir)
            self.api_thread = threading.Thread(target=self._start_api, daemon=True)
            self.api_thread.start()

            self.voila = self._open_voila()

            time.sleep(1)
            webbrowser.open_new_tab(f'http://{self.api_host}:{self.api_port}/')
            self.voila.wait()

        except KeyboardInterrupt:
            print('KeyboardInterrupt received, stopping the server.')
            if self.voila:
                self.voila.terminate()

            if os.path.exists(self.tmp_dir):
                shutil.rmtree(self.tmp_dir)