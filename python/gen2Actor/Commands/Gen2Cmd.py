#!/usr/bin/env python

from importlib import reload

import datetime
import logging
import re

import opscore.protocols.keys as keys
import opscore.protocols.types as types
from opscore.utility.qstr import qstr
from pfs.utils import butler

from opdb import opdb as sptOpdb

# from obdb import opdb
from pfs.utils import opdb
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
            ('getVisit', '[<caller>]', self.getVisit),
            ('updateTelStatus', '[<caller>]', self.updateTelStatus),
            ('gen2Reload', '', self.gen2Reload),
            ('archive', '<pathname>', self.archive),
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

        self.logger = logging.getLogger('Gen2Cmd')
        self.opdb = sptOpdb.OpDB(hostname='db-ics', dbname='opdb', username='pfs')
        self.visit = 0
        self.statusSequence = 0

        self.actor.butler = butler.Butler()
        self.updateArchiving()

    def getDesignId(self, cmd):
        """Return the current designId for the instrument.

        For INSTRM-1102, hardwire to use the DCB key. Once INSTRM-1095
        is done, switch to the IIC key.

        """
        dcbModel = self.actor.models['dcb'].keyVarDict
        iicModel = self.actor.models['iic'].keyVarDict

        if 'designId' in iicModel:
            cmd.warn('text="IIC knows about designIds, but we are forcing the DCB version."')

        designId = dcbModel['designId'].getValue()
        designId = int(designId) # The Long() opscore type yields 0x12345678 values.
        return designId

    def getVisit(self, cmd):
        """Query for a new PFS visit from Gen2.

        This is a critical command for any successful exposure. It really cannot be allowed to fail.

        If we cannot get a new frame id from Gen2 we fail over to a
        filesystem-based sequence. If that fails we blow up.

        We also survive opdb outages. The actors will have to be
        robust against missing pfs_visit table entries.

        """

        caller = str(cmd.cmd.keywords['caller'].values[0]) if 'caller' in cmd.cmd.keywords else None
        description = caller if caller is not None else cmd.cmdr

        self.visit = visit = self.actor.gen2.getPfsVisit()
        self.statusSequence = 0
        try:
            designId = self.getDesignId(cmd)
            designId = int(designId)
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
        """ Archive a FITS file for STARS. """
        pathname = cmd.cmd.keywords['pathname'].values[0]
        gen2 = self.actor.gen2

        gen2.archivePfsFile(pathname)
        cmd.finish(f'text="registered {pathname} for archiving"')

    def updateOpdb(self, cmd, now, statusDict, sky, pointing):
        statusSequence = self.statusSequence
        cmd.debug(f'text="updating opdb.tel_status with visit={self.visit}, '
                  f'sequence={statusSequence}"')
        self.statusSequence += 1

        def gk(name, cmd=cmd, statusDict=statusDict):
            return self._getGen2Key(cmd, name, statusDict=statusDict)

        try:
            stmt = self.opdb.insert_kw('tel_status',
                                       pfs_visit_id=self.visit, status_sequence_id=statusSequence,
                                       altitude=gk('ALTITUDE'), azimuth=gk('AZIMUTH'),
                                       insrot=gk('INR-STR'), adc_pa=gk('ADC-STR'),
                                       m2_pos3=gk('M2-POS3'),
                                       tel_ra=pointing.ra.degree, tel_dec=pointing.dec.degree,
                                       dome_shutter_status=-9998, dome_light_status=-9998,
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
        raStr = sky.ra.to_string(unit=u.hourangle, sep=':', precision=3, pad=True)
        decStr = sky.dec.to_string(unit=u.degree, sep=':', precision=3, pad=True, alwayssign=True)

        pointing = astropy.coordinates.SkyCoord(f'{gk("RA_CMD")} {gk("DEC_CMD")}',
                                                unit=(u.hourangle, u.deg),
                                                frame=astropy.coordinates.FK5)
        pointingRaStr = pointing.ra.to_string(unit=u.hourangle, sep=':', precision=3, pad=True)
        pointingDecStr = pointing.dec.to_string(unit=u.degree, sep=':', precision=3, pad=True, alwayssign=True)

        if caller is not None:
            statusSequence = self.updateOpdb(cmd, now, statusDict, sky, pointing)

        cmd.inform(f'inst_ids="NAOJ","Subaru","PFS"')
        cmd.inform(f'program={qstr(gk("PROP-ID"))},{qstr(gk("OBS-MOD"))},{qstr(gk("OBS-ALOC"))},{qstr(gk("OBSERVER"))}')

        cmd.inform(f'object={qstr(gk("OBJECT"))},{qstr(raStr)},{qstr(decStr)},{qstr(raStr)},{qstr(decStr)}')
        cmd.inform(f'pointing={qstr(pointingRaStr)},{qstr(pointingDecStr)}')
        cmd.inform(f'offsets={gk("W_RAOFF"):0.4f},{gk("W_DECOFF"):0.4f}')
        #
        cmd.inform(f'coordinate_system_ids="FK5",180.0,{gk("EQUINOX")}')
        cmd.inform(f'tel_axes={gk("AZIMUTH"):0.4f},{gk("ALTITUDE"):0.4f},{gk("ZD")},{gk("AIRMASS"):0.3f}')
        cmd.inform(f'tel_rot={gk("INST-PA")},{gk("INR-STR")}')
        cmd.inform(f'tel_focus={qstr(gk("TELFOCUS"))},{qstr(gk("FOC-POS"))},{gk("FOC-VAL")}')
        cmd.inform(f'tel_adc={qstr(gk("ADC-TYPE"))},{gk("ADC-STR")}')
        cmd.inform(f'dome_env={gk("DOM-HUM"):0.3f},{gk("DOM-PRS"):0.3f},{gk("DOM-TMP"):0.3f},{gk("DOM-WND"):0.3f}')
        cmd.inform(f'outside_env={gk("OUT-HUM"):0.3f},{gk("OUT-PRS"):0.3f},{gk("OUT-TMP"):0.3f},{gk("OUT-WND"):0.3f}')
        # cmd.inform(f'pointing={sky.frame.name},{sky.equinox},{sky.ra.deg},{sky.dec.deg}')
        cmd.inform(f'm2={qstr(gk("M2-TYPE"))},{gk("M2-POS1")},{gk("M2-POS2")},{gk("M2-POS3")}')
        cmd.inform(f'm2rot={gk("M2-ANG1"):0.4f},{gk("M2-ANG2"):0.4f},{gk("M2-ANG3"):0.4f}')
        cmd.inform(f'pfuOffset={gk("W_M2OFF1"):0.3f},{gk("W_M2OFF2"):0.3f},{gk("W_M2OFF3"):0.3f}')
        cmd.inform(f'autoguider={qstr(gk("AUTOGUID"))}')
        cmd.inform(f'conditions={qstr(gk("WEATHER"))},{gk("SEEING"):0.3f},{gk("TRANSP"):0.3f}')

        if caller is not None:
            cmd.inform(f'statusUpdate={self.visit},{statusSequence},{caller}')
