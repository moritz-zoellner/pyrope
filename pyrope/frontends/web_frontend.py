import subprocess, os, shutil, threading

import nbformat

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
import uvicorn
import webbrowser, time


class WebFrontend:

    def __init__(self, pool):
        self.pool = pool
        self.voila = None
        self.tmp_dir = 'tmp_dir'

        self.app = FastAPI()
        self.api_host = "127.0.0.1"
        self.api_port = 8000
        self._setup_api()

# ------ Rest API ------

    def _setup_api(self):
        self.app.mount("/static", StaticFiles(directory="pyrope/web/static"), name="static")
        templates = Jinja2Templates(directory="pyrope/web/templates")

        @self.app.get("/", response_class=HTMLResponse)
        def root(request: Request):
            return templates.TemplateResponse("index.html", {"request": request})

        @self.app.get("/structure")
        def get_structure():
            return self._pool_to_dict(self.pool)
    
    def _start_api(self):
        uvicorn.run(self.app, host=self.api_host, port=self.api_port, log_level="info")

    def _pool_to_dict(self, pool):
        from pyrope.core import Exercise, ExercisePool

        items = []
        count_exercises = 0
        count_pools = 0

        for item in pool:
            if isinstance(item, Exercise):
                items.append({"type": "exercise", "name": f'{count_exercises}_{item.__class__.__name__}.ipynb'})
                count_exercises += 1
            elif isinstance(item, ExercisePool):
                items.append({"type": "pool", "name": f'{count_pools}_subpool'  , "items": self._pool_to_dict(item)})
                count_pools += 1
        return items
    

# ------ Voila/Notebooks ------  

    def _build_notebook_dir(self, pool, dir):
        from pyrope.core import Exercise, ExercisePool

        os.makedirs(dir, exist_ok=True)
        count_exercises = 0
        count_pools = 0

        for item in pool:
            if isinstance(item, Exercise):

                name = item.__class__.__name__
                module = item.__class__.__module__

                nb = nbformat.v4.new_notebook()

                code = (
                    'import sys, os\n'
                    'sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..")))\n'
                    'import pyrope\n' \
                    f'from {module} import {name}\n'
                    f'{name}().run()'
                )
                
                nb['cells'] = [nbformat.v4.new_code_cell(code)]

                file = f'{count_exercises}_{name}.ipynb'
                with open(os.path.join(dir, file), 'w') as f:
                    nbformat.write(nb, f)

                count_exercises += 1

            elif isinstance(item, ExercisePool):
                subdir = os.path.join(dir, f'{count_pools}_subpool')
                self._build_notebook_dir(item, subdir)

                count_pools += 1

    def _open_voila(self):
        process = subprocess.Popen([
            "voila",
            "tmp_dir",
            "--no-browser",
            "--Voila.tornado_settings={\"headers\":{\"Content-Security-Policy\":\"frame-ancestors self *\" }}"
        ])
        return process

# ------ Put it all together ------

    def run(self):
        try:
            self._build_notebook_dir(self.pool, self.tmp_dir)
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