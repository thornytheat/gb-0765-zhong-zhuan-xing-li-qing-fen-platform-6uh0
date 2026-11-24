from . import service  # noqa
from .service import *  # noqa


def __getattr__(name):
    return getattr(service, name)
