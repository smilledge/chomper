import inspect
from copy import copy, deepcopy

from chomper.utils import iter_methods


class Signature(object):

    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.args = list(args)
        self.kwargs = kwargs

    def __repr__(self):
        return 'Signature(%s, %s, %s)' % (self.name, self.args, self.kwargs)

    def __copy__(self):
        return self.__class__(self.name, *copy(self.args), **copy(self.kwargs))


def _replace_init(cls):
    def __init__(self, *args, **kwargs):
        self._signatures = [Signature('__init__', *args, **kwargs)]
    cls.__init__ = __init__


def _replace_methods(cls):
    for name, method in iter_methods(cls):
        if not name.startswith('__'):
            setattr(cls, name, _make_replaced_method(name))


def _make_replaced_method(name):
    def record(self, *args, **kwargs):
        self._signatures.append(Signature(name, *args, **kwargs))
        return self
    record.__name__ = name
    return record


def _add_methods(cls):
    def iter_signatures(self):
        return (sig for sig in self._signatures)
    iter_signatures.__name__ = '__iter__'
    setattr(cls, '__iter__', iter_signatures)


def Replayable(cls):
    cls_clone = type('Replayable%s' % cls.__name__, cls.__bases__, dict(cls.__dict__))
    _replace_init(cls_clone)
    _replace_methods(cls_clone)
    _add_methods(cls_clone)
    return cls_clone


def replay(recorder, target, filter=None):
    _result = None
    is_class = inspect.isclass(target)

    for idx, signature in enumerate(recorder):
        # If it's an instance remove the initial signature for calling __init__
        if idx == 0 and not is_class:
            continue

        if callable(filter):
            signature = filter(deepcopy(signature))

        func = getattr(target, signature.name)
        _result = func(*signature.args, **signature.kwargs)

    return _result
