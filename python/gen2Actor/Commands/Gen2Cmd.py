#!/usr/bin/env python

from importlib import reload

import datetime
import logging
import os
import re

import numpy as np

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr
from pfs.utils import butler

from opdb import opdb as sptOpdb

# from obdb import opdb
from ics.utils import opdb
import astropy.coordinates
import astropy.units as u

reload(sptOpdb)

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
            ('getVisit', '[<caller>] [<designId>]', self.getVisit),
            ('updateTelStatus', '[<caller>]', self.updateTelStatus),
            ('gen2Reload', '', self.gen2Reload),
            ('archive', '<pathname>', self.archive),
            ('updateArchiving', '', self.updateArchiving),
            ('makeTables', '', self.makePfsTables),
            ('setupCallbacks', '', self.setupCallbacks),
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
                                        keys.Key("designId", types.Long(), help="PFS design ID"),

                                        )

        self.logger = logging.getLogger('Gen2Cmd')
        self.opdb = sptOpdb.OpDB(hostname='db-ics', dbname='opdb', username='pfs')
        self.visit = 0
        self.statusSequence = 0

        self.actor.butler = butler.Butler()
        self.setupCallbacks()
        self.updateArchiving()

    def getDesignId(self, cmd):
        """Return the current designId for the instrument.

        For INSTRM-1102, hardwire to use the DCB key. Once INSTRM-1095
        is done, switch to the IIC key.

        """
        cmdKeys = cmd.cmd.keywords
        designId = str(cmdKeys['designId'].values[0]) if 'designId' in cmdKeys else None

        if designId is None:
            dcbModel = self.actor.models['dcb'].keyVarDict
            iicModel = self.actor.models['iic'].keyVarDict
            if 'designId' in iicModel:
                cmd.warn('text="No designId specified, using the IIC version."')
                designId = iicModel['designId'].getValue()
            elif 'designId' in dcbModel:
                cmd.warn('text="No designId specified, using the DCB version."')
                designId = dcbModel['designId'].getValue()
            else:
                cmd.warn('text="No designId specified, using 0."')
                designId = 0

        try:
            designId = int(designId) # The Long() opscore type yields 0x12345678 values.
        except TypeError:
            designId = 0

        return designId

    def getVisit(self, cmd):
        """Query for a new PFS visit from Gen2.

        This is a critical command for any successful exposure. It really cannot be allowed to fail.

        If we cannot get a new frame id from Gen2 we fail over to a
        filesystem-based sequence. If that fails we blow up.

        We also survive opdb outages. The actors will have to be
        robust against missing pfs_visit table entries.

        """
        cmdKeys = cmd.cmd.keywords
        caller = str(cmdKeys['caller'].values[0]) if 'caller' in cmdKeys else None
        description = caller if caller is not None else cmd.cmdr

        self.visit = visit = self.actor.gen2.getPfsVisit()
        self.statusSequence = 0
        try:
            designId = self.getDesignId(cmd)
        except Exception as e:
            cmd.warn(f'text="failed to get designId: {e}"')
            designId = -9999

        cmd.debug(f'text="updating opdb.pfs_visit with visit={visit}, design_id={designId}, '
                  f'and description={description}"')
        try:
            opdb.opDB.insert('pfs_visit', pfs_visit_id=visit, pfs_visit_description=description,
                             pfs_design_id=designId, issued_at='now')
        except Exception as e:
            cmd.warn('text="failed to insert into pfs_visit: %s"' % (e))

        self._genActorKeys(cmd, caller=caller)

        cmd.finish('visit=%d' % (visit))

    def updateTelStatus(self, cmd):
        """Query for a new PFS status info.

        This generates MHS keywords for the telescope and observing conditions. If the caller
        is known to be from a camera system, the opdb tables are also updated.
        """

        caller = cmd.cmd.keywords['caller'].values[0] if 'caller' in cmd.cmd.keywords else None

        self._genActorKeys(cmd, caller=caller)

        cmd.finish()

    def gen2Reload(self, cmd):
        gen2 = self.actor.gen2

        gen2._reload()
        gen2.registerStatusDict()

        self.updateArchiving(cmd)

    def doArchivePath(self, path, filetype, frameId=None):
        """Arrange for Gen2 archiving of a single file path

        Args:
        -----
        path : pathlike
           a fully resolved path to archive
        filetype : `str`
           a friendly identifier for messages. e.g. 'PFSA'
        frameId : `str`
           a Gen2-sourced frameId to identify the path. Only used
           when the id does not match the filename (PFSF v. pfsConfig)
        """

        if path is None or not os.path.exists(path):
            self.logger.warning(f'NOT archiving nonexistant {filetype} file {path}')
            return
        
        self.logger.info(f'requesting archiving of {filetype} {path}')
        try:
            self.actor.gen2.archivePfsFile(str(path), frameId=frameId)
        except Exception as e:
            self.logger.warning(f'failed to archive {filetype} file {path}: {e}')
        
    def newPfscFilename(self, keyvar):
        """ Callback for instrument 'filename' keyword updates. """
        try:
            path = keyvar.getValue()
        except ValueError:
            self.logger.warn('failed to handle new filename keyvar for %s', keyvar)
            return

        self.doArchivePath(path, 'PFSC')

    def newPfsaFileIds(self, keyvar):
        """Archive a PFSA file described by a ccd_mn.spsFileIds keyvar. """

        vals = keyvar.valueList
        names = 'cam', 'pfsDay', 'visit', 'spectrograph', 'armNum'
        idDict = dict(zip(names, vals))
        self.logger.info(f'getPath(spsFile) with {idDict} ')

        try:
            path = self.actor.butler.getPath('spsFile', idDict)
        except Exception as e:
            self.logger.warning(f'getPath(spsFile) with {idDict} failed: {e}')
            return

        self.doArchivePath(path, 'PFSA')

    def newPfsbFileIds(self, keyvar):
        """Archive a PFSB file described by a hx_mn.spsFileIds keyvar. """

        vals = keyvar.valueList
        path = vals[0]

        self.doArchivePath(path, 'PFSB')

    def newPfscFileIds(self, keyvar):
        """Archive a PFSC file described by a mcs.mcsFileIds keyvar. """

        vals = keyvar.valueList
        names = 'pfsDay', 'visit', 'frame'
        idDict = dict(zip(names, vals))
        self.logger.info(f'getPath(mcsFile) with {idDict} ')

        try:
            path = self.actor.butler.getPath('mcsFile', idDict)
        except Exception as e:
            self.logger.warning(f'getPath(mcsFile) with {idDict} failed: {e}')
            return

        self.doArchivePath(path, 'PFSC')

    def newPfsdFileIds(self, keyvar):
        """Archive a PFSD file described by an agcc.agccFileIds keyvar. """

        vals = keyvar.valueList
        names = 'pfsDay', 'visit', 'agccFrameNum'
        idDict = dict(zip(names, vals))
        self.logger.info(f'getPath(agccFile) with {idDict} ')

        try:
            path = self.actor.butler.getPath('agccFile', idDict)
        except Exception as e:
            self.logger.warning(f'getPath(agccFile) with {idDict} failed: {e}')

        self.doArchivePath(path, 'PFSD')

    def newPfsConfig(self, keyvar):
        """Archive a pfsConfig file described by an iic.pfsConfig keyvar. """

        vals = keyvar.valueList
        idDict = dict(pfsConfigId=vals[0],
                      visit=vals[1],
                      pfsDay=vals[2])
        try:
            path = self.actor.butler.getPath('pfsConfig', idDict)
            frameId = f'PFSF{idDict["visit"]:06d}00'
        except Exception as e:
            self.logger.warning(f'getPath(pfsConfig) with {idDict} failed: {e}')

        self.doArchivePath(path, 'PFSF', frameId=frameId)

    def newPfsDesign(self, keyvar):
        """MHS keyvar callback to pass PfsDesign info over to Gen2 """

        vals = keyvar.valueList
        tvals = [v.__class__.baseType(v) for v in vals]

        names = 'designId', 'visit', 'ra', 'dec', 'pa', 'name'
        keyDict = dict(zip(names, tvals))

        tableName = 'PFS.DESIGN'
        self.logger.info('newPfsDesign: %s', keyDict)
        self.updateStatusDict(tableName, keyDict)

    def newGuideErrors(self, keyvar):
        """MHS keyvar callback to pass guideOffsets info over to Gen2

        Key('guideErrors',
            Int(name='exposureId', help='Exposure identifier'),
            Float(name='dRA', units='arcsec', help='Right ascension error'),
            Float(name='dDec', units='arcsec', help='Declination error'),
            Float(name='dInR', units='arcsec', help='Instrument rotator angle error'),
            Float(name='dAz', units='arcsec', help='Azimuth error'),
            Float(name='dAlt', units='arcsec', help='Altitude error'),
            Float(name='dZ', units='mm', help='Focus error'),
            Float(name='dScale', help='Scale error'),
        ),
        """

        vals = keyvar.valueList
        tvals = [v.__class__.baseType(v) for v in vals]

        names = 'exposureId', 'dRA', 'dDec', 'dInR', 'dAz', 'dAlt', 'dFocus', 'dScale'
        keyDict = dict(zip(names, tvals))

        tableName = 'PFS.AG.ERR'
        self.logger.info('newGuideErrors: %s', keyDict)
        self.updateStatusDict(tableName, keyDict)

    def newGroupId(self, keyvar):
        """MHS keyvar callback to pass iic.groupId info over to Gen2

        Key("groupId",
            Int(name="gid", help="groupId"),
            String(name="name", help="groupName"))
        ),
        """

        gid, gname = keyvar.valueList
        tableName = 'PFS.GROUPID'
        now = datetime.datetime.now()
        nowStr = datetime.datetime.strftime(now, "%Y-%m-%dT%H:%M:%S")

        keyDict = dict(groupId=int(gid), groupName=str(gname), date=nowStr)
        self.logger.info('newGroupId(%s): %s', gname, keyDict)
        self.updateStatusDict(tableName, keyDict)

    def newPfiShutters(self, keyvar):
        """MHS keyvar callback to pass sps.pfiShutter info over to Gen2

        Key('pfiShutters',
            Enum('close', 'open', 'undef'))
        """

        state, = keyvar.valueList
        tableName = 'PFS.SHUTTERS'
        now = datetime.datetime.now()
        nowStr = datetime.datetime.strftime(now, "%Y-%m-%dT%H:%M:%S")

        keyDict = dict(state=str(state), date=nowStr)
        self.logger.info('pfiShutter: %s', keyDict)
        self.updateStatusDict(tableName, keyDict)

    def _updateCallback(self, actor, keyname, callback=None):
        """Update a keyvar callback, deleting existing one if necessary.

        Args
        ----
        actor : `str`
           Name of actor
        keyname : `str`
           Name of actor's keyword.
        callback : callable
           function to call when a new value is received.
        """

        try:
            model = self.actor.models[actor].keyVarDict
        except Exception as e:
            self.logger.warn(f'failed to load model {actor}: {e}')
            return

        try:
            model[keyname]._removeAllCallbacks()
            if callback is not None:
                model[keyname].addCallback(callback, callNow=False)
            self.logger.info(f'added callback {callback} for {actor}.{keyname}')
        except Exception as e:
            self.logger.warn(f'failed to add callback for {keyname}: {e}')
            return

    def setupCallbacks(self, cmd=None):
        if cmd is None:
            cmd = self.actor.bcast

        if 'iic' not in self.actor.models:
            cmd.warn("text='not updating callbacks until iic model loaded.....'")
            return

        if 'PFS.DESIGN' not in self.actor.gen2.keyTables:
            self.makePfsTables(cmd)

        self._updateCallback('iic', 'pfsDesign', self.newPfsDesign)
        self._updateCallback('ag', 'guideErrors', self.newGuideErrors)
        self._updateCallback('iic', 'groupId', self.newGroupId)
        self._updateCallback('sps', 'pfiShutters', self.newPfiShutters)

        cmd.inform(f'text="Gen2 key tables: {self.actor.gen2.keyTables.keys()}"')
        cmd.finish()

    def makePfsTables(self, cmd):
        keyMap = dict(ra='RA', dec='DEC', pa='PA',
                      visit='VISIT', designId='ID', name='NAME')

        dictName = 'PFS.DESIGN'
        self.newStatusDict(dictName, keyMap, cmd)
        self.actor.gen2.ocs.setStatus(dictName, ra=np.nan, dec=np.nan, pa=np.nan, 
                                      visit=None, designId=0xdeaddeadbeef, name='test table')
        self.actor.gen2.ocs.exportStatusTable(dictName)

        agKeys = dict(dRA='RA_ERR', dDec='DEC_ERR', dInR='INR_ERR',
                      dAz='AZ_ERR', dAlt='ALT_ERR', dFocus='FOCUS_ERR',
                      exposureId='EXPID', dScale='SCALE_ERR')
        dictName = 'PFS.AG.ERR'
        self.newStatusDict(dictName, agKeys, cmd)

        self.actor.gen2.ocs.setStatus(dictName, dRA=np.nan, dDec=np.nan, dInR=np.nan, 
                                      dAz=np.nan, dAlt=np.nan, dFocus=np.nan, dScale=np.nan,
                                      exposureId=0)
        self.actor.gen2.ocs.exportStatusTable(dictName)

        dictName = "PFS.GROUPID"
        self.newStatusDict(dictName, dict(groupId="ID", groupName="NAME", date="DATE"),
                           cmd)
        self.actor.gen2.ocs.setStatus(dictName, groupId=-1, groupName="nogroup", date="never")
        self.actor.gen2.ocs.exportStatusTable(dictName)

        dictName = "PFS.SHUTTERS"
        self.newStatusDict(dictName, dict(state="STATE", date="DATE"),
                           cmd)
        self.actor.gen2.ocs.setStatus(dictName, state="unknown", date="never")
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

        gen2.ocs.setStatus(dictName, **keys)
        gen2.ocs.exportStatusTable(dictName)

    def updateArchiving(self, cmd=None):
        """Reconfigure and regenerate all archiving keyvar callbacks.

        This can be called as a command, in which case all known
        archiver keyvar callbacks are either cleared are registered.

        This is also called everytime this module is reloaded, so that
        the callbacks point to methods in the new object.

        Uses the gen2.archive configuration variable to specify which
        files we want archived. A list of 'PFSC', 'PFSD', 'ccd_nm',
        'hx_nm', 'pfsConfig'
        """

        if cmd is None:
            cmd = self.actor.bcast

        doArchive = self.actor.actorConfig['gen2']['archive']

        if 'fps' not in self.actor.models:
            cmd.warn("text='not updating callbacks until models loaded.....'")
            return

        self._updateCallback('iic', 'pfsConfig',
                             self.newPfsConfig if 'pfsConfig' in doArchive else None)
        self._updateCallback('mcs', 'mcsFileIds',
                             self.newPfscFileIds if 'PFSC' in doArchive else None)
        self._updateCallback('agcc', 'pfsdPathIds',
                             self.newPfsdFileIds if 'PFSD' in doArchive else None)

        for sm in 1,2,3,4:
            for arm in 'b','r':
                camName=f'{arm}{sm}'
                actorName = f'hx_{camName}' if arm == 'n' else f'ccd_{camName}'
                self._updateCallback(actorName, 'spsFileIds',
                                     self.newPfsaFileIds if camName in doArchive else None)
            for arm in 'n',:
                camName=f'{arm}{sm}'
                actorName = f'hx_{camName}'
                self.logger.info(f'wiring in {actorName}.filename if {camName in doArchive}')
                self._updateCallback(actorName, 'filename',
                                     self.newPfsbFileIds if camName in doArchive else None)

        if cmd is not None:
            cmd.finish(f'text="archiving {doArchive}')

    def _updateCallbacks(self):
        self.updateArchiving()

        # fpsModel = self.models['fps'].keyVarDict
        # fpsModel['mcsBoresight'].addCallback(self.gen2.newMcsBoresight, callNow=False)

    def archive(self, cmd):
        """ Archive a FITS file for STARS. """
        pathname = cmd.cmd.keywords['pathname'].values[0]
        gen2 = self.actor.gen2

        gen2.archivePfsFile(str(pathname))
        cmd.finish(f'text="registered {pathname} for archiving"')

    def updateOpdb(self, cmd, now, statusDict, sky, pointing, caller):
        statusSequence = self.statusSequence
        cmd.debug(f'text="updating opdb.tel_status with visit={self.visit}, '
                  f'sequence={statusSequence}, caller={caller}"')
        self.statusSequence += 1

        def gk(name, cmd=cmd, statusDict=statusDict):
            return self._getGen2Key(cmd, name, statusDict=statusDict)

        try:
            self.opdb.insert_kw('tel_status',
                                pfs_visit_id=self.visit, status_sequence_id=statusSequence,
                                altitude=gk('ALTITUDE'), azimuth=gk('AZIMUTH'),
                                insrot=gk('INR-STR'), inst_pa=gk('INST-PA'),
                                adc_pa=gk('ADC-STR'),
                                m2_pos3=gk('M2-POS3'),
                                tel_ra=pointing.ra.degree, tel_dec=pointing.dec.degree,
                                dome_shutter_status=-9998, dome_light_status=-9998,
                                dither_ra=gk('W_DTHRA'), dither_dec=gk('W_DTHDEC'), dither_pa=gk('W_DTHPA'),
                                caller=caller,
                                created_at=now.isoformat())
        except Exception as e:
            cmd.warn('text="failed to insert into tel_status: %s"' % (e))

        try:
            self.opdb.insert_kw('env_condition',
                                pfs_visit_id=self.visit, status_sequence_id=statusSequence,
                                dome_temperature=gk('DOM-TMP'), dome_pressure=gk('DOM-PRS'),
                                dome_humidity=gk('DOM-HUM'),
                                outside_temperature=gk('OUT-TMP'), outside_pressure=gk('OUT-PRS'),
                                outside_humidity=gk('OUT-HUM'),
                                created_at=now.isoformat())
        except Exception as e:
            cmd.warn('text="failed to insert into env_condition: %s"' % (e))

        return statusSequence

    def _latchStatusDict(self,cmd):
        return self.actor.gen2.statusDictTel.copy()

    def _getGen2Key(self, cmd, name, statusDict=None):
        """ Utility to wrap fetching Gen2 keyword values.

        Bugs
        ----

        This is disgustingly intimate with the current PFS.py internals. Those will change.
        """

        if statusDict is None:
            statusDict = self._latchStatusDict()

        try:
            hdr1 = self.actor.gen2.tel_header[name]
            val = statusDict[hdr1[0]]
            valType = hdr1[2]
        except:
            cmd.warn(f'text="FAILED to retrieve {name}"')
            return None

        try:
            val = valType(val)
        except:
            cmd.warn(f'text="FAILED to convert {name}:{val} as a {valType}"')

        return val

    def _getScreenState(self, cmd, frontPos, rearPos):
        """Clean up and pin down FF screen positions.

        Parameters
        ----------
        cmd : `Command`
            The Command to report errors to.
        frontPos : `float`
            The position of the front edge of the screen in m. Noisy.
        rearPos : `float`
            The position of the rear edge of the screen in m. Noisy.

        Returns
        -------
        frontPos : `float`
            The position of the front edge of the screen in m. Rounded.
        rearPos : `float`
            The position of the rear edge of the screen in m. Rounded.
        screenPos : `str`
            The screen position, one of "sky", "screen", "covered", or "unknown".
        """
        knownPositions = {(3.0, 1.5):"sky",
                          (24.0, 22.5):"instrument exchange",
                          (24.3, 1.5):"sky_highalt", # alt > 85deg, sometimes. The front screen
                                                     # is actually at a hard limit at 24.265
                          (16.0, 10.0):"corrugated_screen",
                          (17.0, 1.5):"flat_screen",
                          }
        frontPosP = frontPosR = np.round(frontPos, 1)
        rearPosP = rearPosR = np.round(rearPos, 1)

        # There are only a few commonly missed positions:
        if frontPosP >= 23.9:
            frontPosP = 24.3
        elif frontPosP >= 15.9 and frontPosP < 16.1:
            frontPosP = 16.0
        elif frontPosP < 3.1:
            frontPosP = 3.0

        screenPos = knownPositions.get((frontPosP, rearPosP), "unknown")
        if screenPos == "unknown":
            self.logger.warn(f'unknown screen position: front={frontPos},{frontPosR}, rear={rearPos},{rearPosR}')
        return frontPosR, rearPosR, screenPos

    def _getShutterPos(self, cmd, rawPos):
        """Clean up and pin down shutter position.

        Parameters
        ----------
        cmd : `Command`
            The Command to report errors to.
        rawPos : `str`
            The raw shutter position from Gen2.

        Returns
        -------
        shutterPos : `str`
            The shutter position, one of "open", "closed", or "unknown".
        """
        rawPos = rawPos.lower()
        if rawPos == '':
            rawPos = 'unknown'
        return rawPos

    def _genActorKeys(self, cmd, caller=None, doGen2Refresh=True):
        """Generate all gen2 status keys.

        For this actor, this might get called from either the gen2 or the MHS sides.

        Bugs
        ---

        With the current Gen2 keyword table implementation, we do not
        correctly set invalid values to the right type. I think the
        entire mechanism will be changed.

        """

        if doGen2Refresh:
            self.actor.gen2.update_header_stat()
        tz = datetime.timezone(datetime.timedelta(hours=-10), "HST")
        now = datetime.datetime.now(tz=tz)
        statusDict = self._latchStatusDict(cmd)

        def gk(name, cmd=cmd, statusDict=statusDict):
            return self._getGen2Key(cmd, name, statusDict=statusDict)

        sky = astropy.coordinates.SkyCoord(f'{gk("RA")} {gk("DEC")}',
                                           unit=(u.hourangle, u.deg),
                                           frame=astropy.coordinates.FK5)
        raStr = sky.ra.to_string(unit=u.hourangle, sep=':', precision=2, pad=True)
        decStr = sky.dec.to_string(unit=u.degree, sep=':', precision=2, pad=True, alwayssign=True)

        pointing = astropy.coordinates.SkyCoord(f'{gk("RA_CMD")} {gk("DEC_CMD")}',
                                                unit=(u.hourangle, u.deg),
                                                frame=astropy.coordinates.FK5)
        pointingRaStr = pointing.ra.to_string(unit=u.hourangle, sep=':', precision=2, pad=True)
        pointingDecStr = pointing.dec.to_string(unit=u.degree, sep=':',
                                                precision=2, pad=True, alwayssign=True)

        if caller is not None:
            statusSequence = self.updateOpdb(cmd, now, statusDict, sky, pointing, caller)

        cmd.inform('inst_ids="NAOJ","Subaru","PFS"')
        cmd.inform(f'program={qstr(gk("PROP-ID"))},{qstr(gk("OBS-MOD"))},'
                   f'{qstr(gk("OBS-ALOC"))},{qstr(gk("OBSERVER"))}')

        cmd.inform(f'object={qstr(gk("OBJECT"))},{qstr(raStr)},{qstr(decStr)},{qstr(raStr)},{qstr(decStr)}')
        cmd.inform(f'pointing={qstr(pointingRaStr)},{qstr(pointingDecStr)}')
        cmd.inform(f'offsets={gk("W_RAOFF"):0.4f},{gk("W_DECOFF"):0.4f}')
        cmd.inform(f'telDither={gk("W_DTHRA"):0.3f},{gk("W_DTHDEC"):0.3f},{gk("W_DTHPA"):0.3f}')
        cmd.inform(f'telGuide={gk("W_AGRA"):0.3f},{gk("W_AGDEC"):0.3f},{gk("W_AGINR"):0.3f}')
        #
        cmd.inform(f'coordinate_system_ids="FK5",180.0,{gk("EQUINOX")}')
        cmd.inform(f'tel_axes={gk("AZIMUTH"):0.4f},{gk("ALTITUDE"):0.4f},{gk("ZD"):0.5f},{gk("AIRMASS"):0.3f}')
        cmd.inform(f'tel_rot={gk("INST-PA")},{gk("INR-STR")}')
        cmd.inform(f'tel_focus={qstr(gk("TELFOCUS"))},{qstr(gk("FOC-POS"))},{gk("FOC-VAL")}')
        cmd.inform(f'tel_adc={qstr(gk("ADC-TYPE"))},{gk("ADC-STR")}')
        cmd.inform(f'dome_env={gk("DOM-HUM"):0.3f},{gk("DOM-PRS"):0.3f},'
                   f'{gk("DOM-TMP"):0.3f},{gk("DOM-WND"):0.3f}')
        cmd.inform(f'outside_env={gk("OUT-HUM"):0.3f},{gk("OUT-PRS"):0.3f},'
                   f'{gk("OUT-TMP"):0.3f},{gk("OUT-WND"):0.3f}')
        # cmd.inform(f'pointing={sky.frame.name},{sky.equinox},{sky.ra.deg},{sky.dec.deg}')
        cmd.inform(f'm2={qstr(gk("M2-TYPE"))},{gk("M2-POS1")},{gk("M2-POS2")},{gk("M2-POS3")}')
        cmd.inform(f'm2rot={gk("M2-ANG1"):0.4f},{gk("M2-ANG2"):0.4f},{gk("M2-ANG3"):0.4f}')
        cmd.inform(f'pfuOffset={gk("W_M2OFF1"):0.3f},{gk("W_M2OFF2"):0.3f},{gk("W_M2OFF3"):0.3f}')
        cmd.inform(f'autoguider={qstr(gk("AUTOGUID"))}')
        cmd.inform(f'conditions={qstr(gk("WEATHER"))},{gk("SEEING"):0.3f},{gk("TRANSP"):0.3f}')

        cmd.inform(f'moon={gk("MOON-EL"):0.3f},{gk("MOON-SEP"):0.3f},{gk("MOON-ILL"):0.3f}')
        cmd.inform(f'obsMethod={qstr(gk("OBS-MTHD"))}')

        screenFront, screenRear, screenPos = self._getScreenState(cmd, gk("W_TFFSFP"), gk("W_TFFSRP"))
        domeShutter = self._getShutterPos(cmd, gk("W_TSHUTR"))

        cmd.inform(f'domeLights={gk("W_TDLGHT")}; domeShutter={domeShutter}; '
                   f'topScreenPos={screenFront:0.1f},{screenRear:0.1f},{screenPos}')

        cmd.inform(f'ringLampsStatus={gk("W_TFF1ST")},{gk("W_TFF2ST")},{gk("W_TFF3ST")},{gk("W_TFF4ST")}')
        cmd.inform(f'ringLampsCmd={gk("W_TFF1VC"):0.1f},{gk("W_TFF2VC"):0.1f},'
                   f'{gk("W_TFF3VC"):0.1f},{gk("W_TFF4VC"):0.1f}')
        cmd.inform(f'ringLamps={gk("W_TFF1VV"):0.1f},{gk("W_TFF2VV"):0.1f},'
                   f'{gk("W_TFF3VV"):0.1f},{gk("W_TFF4VV"):0.1f}')

        if caller is not None:
            cmd.inform(f'statusUpdate={self.visit},{statusSequence},{caller}')
