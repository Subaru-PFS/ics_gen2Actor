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
            ('makeTables', '', self.makePfsTables),
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

        cmd.inform(f'text="{varname} = {res}"')
        cmd.finish()

    def makePfsTables(self, cmd):
        keyMap = dict(ra='RA', dec='DEC', pa='PA',
                      visit='VISIT', id='ID', name='NAME')
        
        dictName = 'PFS.DESIGN'
        dd = self.newStatusDict(dictName, keyMap, cmd)
        self.actor.gen2.ocs.setStatus(dictName, ra=np.nan, dec=np.nan, pa=np.nan, 
                                      visit=None, id=0xdeaddeadbeef, name='test table')
        self.actor.gen2.ocs.exportStatusTable(dictName)

        agKeys = dict(dRA='RA_ERR', dDec='DEC_ERR', dInR='INR_ERR',
                      dAz='AZ_ERR', dAlt='ALT_ERR', dFocus='FOCUS_ERR',
                      exposureId='EXPID')
        dictName = 'PFS.AG.ERR'
        d2 = self.newStatusDict(dictName, agKeys, cmd)
        
        self.actor.gen2.ocs.setStatus(dictName, dRA=np.nan, dDec=np.nan, dInR=np.nan, 
                                      dAz=np.nan, dAlt=np.nan, dFocus=np.nan,
                                      exposureId=0)
        self.actor.gen2.ocs.exportStatusTable(dictName)
        
        cmd.finish(f'text="tables={self.actor.gen2.keyTables}"')

    def newStatusDict(self, dictName, keyNameMap, cmd):
        """Create new OCS status table

        Parameters
        ----------
        dictPrefix : `str``
            The full dotted name of our subtable. e.g. "PFS.DESIGN"
        keyNameMap : `dict`
            Mapping from our internal key names to dotted GEN2 names within dictPrefix.
            Note that this is inverted frmo what Gen2 wants.

        Returns
        -------

        table : dict-like
            The created Bunch thing.
        """
        gen2 = self.actor.gen2
        
        ourNames = keyNameMap.keys()
        fullNameMap = dict()
        for k in keyNameMap.keys():
            v = keyNameMap[k]
            fullKey = f'{dictName}.{v}'
            fullNameMap[fullKey] = k

        cmd.diag(f'text="Adding table {dictName} with names = {ourNames} and map {fullNameMap}"')
        gen2.keyTables[dictName] = newTable = gen2.ocs.addStatusTable(dictName, ourNames,
                                                                      None, fullNameMap)
        return newTable

    def updateStatusDict(self, dictName, keys):
        """Update a Gen2 status dict with new values and send it.

        Parameters
        ----------
        dictName : `str`
            The name of the Gen2 status dict table.
            e.g. "PFS.DESIGN"
        keys : `dict`
            The MHS keyword names and values
        """
        gen2 = self.actor.gen2
        
        kd = gen2.keyTables[dictName]
        kd.update(keys)
        gen2.ocs.exportStatusTable(dictName)

