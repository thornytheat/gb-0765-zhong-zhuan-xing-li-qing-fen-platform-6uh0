from app.modules.clock import service as _service
from app.modules.clock.service import *  # noqa


def __getattr__(name):
    try:
        return getattr(_service, name)
    except AttributeError:
        return _service.__getattr__(name)
