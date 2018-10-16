#!/usr/bin/env python

import re

import numpy as np

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr

class Gen2Cmd(object):

    frameInstRE = re.compile('^PF[JLXIASPF][ABCD]')

    def __init__(self, actor):
        # This lets us access the rest of the actor.
        self.actor = actor

        # Declare the commands we implement. When the actor is started
        # these are registered with the parser, which will call the
        # associated methods when matched. The callbacks will be
        # passed a single argument, the parsed and typed command.
        #
        self.vocab = [
            ('getVisit', '', self.getVisit),
            ('gen2Reload', '', self.gen2Reload),
            ('getFitsCards', '<frameId> <expTime> <expType>', self.getFitsCards),
        ]

        # Define typed command arguments for the above commands.
        self.keys = keys.KeysDictionary("core_core", (1, 1),
                                        keys.Key("frameType", types.Enum('A', 'A9'),
                                                 help=''),
                                        keys.Key("cam", types.String(),
                                                 help='camera name, e.g. r1'),
                                        keys.Key("cnt", types.Int(),
                                                 help='a count'),
                                        keys.Key("expType",
                                                 types.Enum('bias', 'dark', 'arc', 'flat', 'object'),
                                                 help='exposure type for FITS header'),
                                        keys.Key("expTime", types.Float(),
                                                 help='exposure time'),
                                        keys.Key("frameId", types.String(),
                                                 help='Gen2 frame ID'),
                                        )

    def getVisit(self, cmd):
        """ Query for a new PFS visit from Gen2.

        This is slightly tricky. OCS allocates 8-digit IDs for single
        image types, but we have four image types (PFS[ABCD]) and only
        want 6-digits of ID.

        So we

        """

        visit = self.actor.gen2.getPfsVisit()

        cmd.finish('visit=%d' % (visit))

    def gen2Reload(self, cmd):
        gen2 = self.actor.gen2
        
        gen2._reload()
        gen2.tel_header = gen2.read_header_list("header_telescope_20160917.txt")
        gen2.statusDictTel = gen2.init_stat_dict(gen2.tel_header)
        cmd.finish()

    def getFitsCards(self, cmd):
        """ Query for all TSC and observatory FITS cards. """

        cmdKeys = cmd.cmd.keywords
        frameId = cmdKeys['frameId'].values[0]
        expTime = cmdKeys['expTime'].values[0]
        expType = cmdKeys['expType'].values[0]

        self.actor._genActorKeys(cmd)

        # Hack to enforce INSTRM-351 temporarily. For shame, CPL
        #
        if not frameId.startswith('PFS'):
            frameId = 'PFSC%06d00' % (int(frameId, base=10))

        hdr = self.actor.gen2.return_new_header(frameId, expType, expTime)
        cmd.inform('header=%s' % (repr(hdr)))
        cmd.finish()
