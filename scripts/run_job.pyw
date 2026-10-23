"""Ejecuta un job sin ventana de consola (lo usa el Programador de tareas): pythonw run_job.pyw <job>"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from pos.cli import app  # noqa: E402

sys.argv = ["pos", "job", "run", *sys.argv[1:]]
app()
