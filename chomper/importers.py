import types
import six
import time
import logging
from copy import copy
from chomper.utils import smart_invoke
from chomper.exceptions import ItemNotImportable
from chomper.pipelines import ItemMeta


class Importer(object):
    """
    Base class for all importers
    """

    name = None
    pipeline = None
    close_when_idle = True

    def __init__(self, name=None, **kwargs):
        if name is not None:
            self.name = name

        if not self.name:
            self.name = type(self).__name__

        if self.pipeline is None:
            self.pipeline = []

        self.items_processed = 0
        self.items_dropped = 0

        for key, value in six.iteritems(kwargs):
            setattr(self, key, value)

    @property
    def logger(self):
        return logging.getLogger(self.name)

    def run(self):
        actions = copy(self.pipeline)
        root_action = actions.pop(0)
        result = root_action()
        meta = ItemMeta()

        self.run_actions(result, meta, actions)

        if not self.close_when_idle:
            time.sleep(1)
            self.run()
        else:
            # TODO: stats might need to be reset (if you call run() twice on the same instance)
            # TODO: need to handle closing pipeline actions here (eg. close database connections)
            self.logger.info('Importer finished, %d items were imported and %d were dropped' %
                             (self.items_processed, self.items_dropped))

    def run_actions(self, result, meta, actions):
        action = actions.pop(0)
        is_generator = isinstance(result, types.GeneratorType)
        is_list = isinstance(result, list) or isinstance(result, tuple)

        if not is_generator and not is_list:
            result = [result]

        for item in result:
            if item is None:
                continue
            try:
                next_result = smart_invoke(action, [item, meta, self])
            except ItemNotImportable as e:
                self.logger.info(e.message)
                self.items_dropped += 1
            else:
                if len(actions):
                    self.run_actions(next_result, ItemMeta.copy_from(meta), copy(actions))
                else:
                    self.items_processed += 1
