#!/usr/bin/env python

from builtins import object
import subprocess

import numpy as np

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr

class TopCmd(object):

    def __init__(self, actor):
        # This lets us access the rest of the actor.
        self.actor = actor

        # Declare the commands we implement. When the actor is started
        # these are registered with the parser, which will call the
        # associated methods when matched. The callbacks will be
        # passed a single argument, the parsed and typed command.
        #
        self.vocab = [
            ('ping', '', self.ping),
            ('status', '', self.status),
            ('inventory', '', self.inventory),
            ('testArgs', '[<cnt>] [<exptime>]', self.testArgs),
        ]

        # Define typed command arguments for the above commands.
        self.keys = keys.KeysDictionary("core_core", (1, 1),
                                        keys.Key("device", types.String(),
                                                 help='device name, probably like bee_r1'),
                                        keys.Key("cam", types.String(),
                                                 help='camera name, e.g. r1'),
                                        keys.Key("cnt", types.Int(),
                                                 help='a count'),
                                        keys.Key("exptime", types.Float(),
                                                 help='exposure time'),
                                        )

    def testArgs(self, cmd):
        cmdKeys = cmd.cmd.keywords
        
        cmd.inform('exptime=%0.2f' % (cmdKeys['exptime'] if 'exptime' in cmdKeys else np.nan))
        cmd.inform('cnt=%d' % (cmdKeys['cnt'] if 'cnt' in cmdKeys else -1))
        cmd.finish()
        
    def inventory(self, cmd):
        """ """
        cmd.finish()
    
    def ping(self, cmd):
        """Query the actor for liveness/happiness."""

        cmd.warn("text='I am an empty and fake actor'")
        cmd.finish("text='Present and (probably) well'")

    def status(self, cmd):
        """Report camera status and actor version. """

        self.actor.sendVersionKey(cmd)
        
        cmd.inform('text="Present!"')
        cmd.finish()

