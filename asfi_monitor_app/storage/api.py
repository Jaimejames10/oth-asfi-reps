"""Fachada interna de persistencia durante la migracion modular."""

from . import database as _database
from .catalog import *
from .credentials import *
from .observations import *
from .obligations import *


def __getattr__(name):
    return getattr(_database, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_database)))
