#!/usr/bin/env python

import numpy as np

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr

class TestCmd(object):

    def __init__(self, actor):
        # This lets us access the rest of the actor.
        self.actor = actor

        # Declare the commands we implement. When the actor is started
        # these are registered with the parser, which will call the
        # associated methods when matched. The callbacks will be
        # passed a single argument, the parsed and typed command.
        #
        self.vocab = [
            ('printStuff', '@raw', self.printStuff),
            ('fix', '', self.fix),
            # ('testTables', '', self.testPfsTables),
        ]

        # Define typed command arguments for the above commands.
        self.keys = keys.KeysDictionary("core_core", (1, 1),
                                        keys.Key("varname", types.String(),
                                                 help='something to print()'),
                                        )

    def fix(self, cmd):
        x, y = self.actor.models["fps"].keyVarDict["mcsBoresight"].valueList
        self.actor.gen2.stattbl1.setvals(mcsBoresight_x=float(x), mcsBoresight_y=float(y))
        cmd.finish()

    def printStuff(self, cmd):
        cmdKeys = cmd.cmd.keywords
        varname = cmdKeys['raw'].values[0]

        res = eval(varname)

        cmd.inform('text=%s' % qstr(f'{varname} = {res}'))
        cmd.finish()
