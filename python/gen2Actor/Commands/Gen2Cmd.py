#!/usr/bin/env python

import re

import numpy as np

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr

from pfs.utils import opdb

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
            ('getVisit', '[<caller>]', self.getVisit),
            ('gen2Reload', '', self.gen2Reload),
            ('archive', '<pathname>', self.archive),
            ('getFitsCards', '<frameId> <expTime> <expType>', self.getFitsCards),
        ]

        # Define typed command arguments for the above commands.
        self.keys = keys.KeysDictionary("core_core", (1, 1),
                                        keys.Key("caller", types.String(),
                                                 help='who should be listed as requesting a visit.'),
                                        keys.Key("frameType", types.Enum('A', 'A9'),
                                                 help=''),
                                        keys.Key("cam", types.String(),
                                                 help='camera name, e.g. r1'),
                                        keys.Key("pathname", types.String(),
                                                 help='full path for a file'),
                                        keys.Key("cnt", types.Int(),
                                                 help='a count'),
                                        keys.Key("expType",
                                                 types.Enum('bias', 'dark', 'arc',
                                                            'flat', 'object', 'acquisition',
                                                            'comparison', 'test'),
                                                 help='exposure type for FITS header'),
                                        keys.Key("expTime", types.Float(),
                                                 help='exposure time'),
                                        keys.Key("frameId", types.String(),
                                                 help='Gen2 frame ID'),
                                        )

    def getVisit(self, cmd):
        """Query for a new PFS visit from Gen2.

        This is a critical command for any successful exposure. It really cannot be allowed to fail.

        If we cannot get a new frame id from Gen2 we fail over to a
        filesystem-based sequence. If that fails we blow up.

        We also survive opdb outages. The actors will have to be
        robust against missing pfs_visit table entries.

        """

        caller = cmd.cmd.keywords['caller'] if 'caller' in cmd.cmd.keywords else cmd.cmdr

        visit = self.actor.gen2.getPfsVisit()

        cmd.debug(f'updating opdb.pfs_visit with visit={visit} and description={caller}')
        try:
            opdb.opDB.insert('pfs_visit', pfs_visit_id=visit, pfs_visit_description=caller)
        except Exception as e:
            cmd.warning('text="failed to insert into pfs_visit: %s"' % (e))

        cmd.finish('visit=%d' % (visit))

    def gen2Reload(self, cmd):
        gen2 = self.actor.gen2
        
        gen2._reload()
        gen2.tel_header = gen2.read_header_list("header_telescope_20160917.txt")
        gen2.statusDictTel = gen2.init_stat_dict(gen2.tel_header)

        mcsModel = self.actor.models['mcs'].keyVarDict
        mcsModel['filename']._removeAllCallbacks()
        mcsModel['filename'].addCallback(self.actor.gen2.newFilePath, callNow=False)
        cmd.inform('text="callback: %s"' % (mcsModel['filename']._callbacks))

        fpsModel = self.actor.models['fps'].keyVarDict
        fpsModel['mcsBoresight']._removeAllCallbacks()
        fpsModel['mcsBoresight'].addCallback(self.actor.gen2.newMcsBoresight, callNow=False)
        cmd.inform('text="callback: %s"' % (fpsModel['mcsBoresight']._callbacks))

        cmd.finish()

    def archive(self, cmd):
        """ Archive a FITS foe the STARS. """
        pathname = cmd.cmd.keywords['pathname'].values[0]
        gen2 = self.actor.gen2

        gen2.archivePfsFile(pathname)
        cmd.finish(f'text="registered {pathname} for archiving"')
        
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

        hdr = self.actor.gen2.return_new_header(frameId, expType, expTime,
                                                doUpdate=False)
        cmd.inform('header=%s' % (repr(hdr)))
        cmd.finish()
