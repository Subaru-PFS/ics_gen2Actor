import collections


class cacheDict(collections.OrderedDict):
    """A dictionary with a limited number of slots.

    When the limit is reached, oldest entries are dropped.
    """

    def __init__(self, size=16, *args, **argv):
        self._maxsize = size
        super().__init__(*args, **argv)

    def __setitem__(self, key, value):
        if self.__len__() >= self._maxsize:
            self.popitem(last=False)
        super().__setitem__(key, value)
