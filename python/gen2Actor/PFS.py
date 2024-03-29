#
# PFS.py -- PFS personality for g2cam instrument interface
#
# Eric Jeschke (eric@naoj.org)
#
"""This file implements the Gen2 client interface for the Prime Focus Spectrograph.
"""

from importlib import reload
import logging
import math
import sys, os, time
import re
import threading
from datetime import datetime, timedelta
import pipes
import base64

import subprocess

import astropy.io.fits as pyfits
from astropy.time import Time

# gen2 base imports
from g2base import Bunch, Task

# g2cam imports
from g2cam.Instrument import BASECAM, CamCommandError
from g2cam.util import common_task

# Value to return for executing unimplemented command.
# 0: OK, non-zero: error
unimplemented_res = 0

class PFSError(CamCommandError):
    pass

class PFS(BASECAM):

    def __init__(self, logger, env, ev_quit=None):

        super(PFS, self).__init__()

        self.logger = logger
        self.env = env
        # Convoluted but sure way of getting this module's directory
        self.mydir = os.path.split(sys.modules[__name__].__file__)[0]

        if not ev_quit:
            self.ev_quit = threading.Event()
        else:
            self.ev_quit = ev_quit

        # Holds our link to OCS delegate object
        self.ocs = None

        # We define our own modes that we report through status
        # to the OCS
        self.mode = 'default'

        # Thread-safe bunch for storing parameters read/written
        # by threads executing in this object
        self.param = Bunch.threadSafeBunch()

        # Interval between status packets (secs)
        self.param.status_interval = 10.0

        self.frameType = 'A'

    def ui(self, camName, options=None, args=None, ev_quit=None, logger=None):
        """ Called early enough that we can wire in an MHS actor thread. """

        logger.warn('in ui -- starting MHS Actor loop.')

        from gen2Actor import main
        self.actor = main.main()
        self.actor.gen2 = self

        # Starts twisted reactor in background thread, command handler
        # in current thread
        self.actor.run()

    #######################################
    # INITIALIZATION
    #######################################

    def read_header_list(self, inlist):
        # open input list and check if it exists
        try:
            fin = open(inlist)
        except IOError:
            raise PFSError("cannot open %s" % str(inlist))

        def convertFloat(raw):
            # Subaru convention
            if raw in {"##NODATA##", "##ERROR##"}:
                return 9998.0
            try:
                f = float(raw)
            except ValueError:
                self.logger.warn("invalid float: %s", raw)
                f = 9998.0
            return f
        
        def convertInt(raw):
            # Subaru convention
            if raw in {"##NODATA##", "##ERROR##"}:
                return 9998
            if isinstance(raw, str):
                return int(raw, base=10)
            else:
                return int(raw)

        StatAlias_list = []
        FitsKey_list = []
        FitsType_list = []
        FitsDefault_list = []
        FitsComment_list = []
        header_num = 0
        for line in fin:
            if not line.startswith('#'):
                param = re.split('[\s!\n\t]+',line[:-1])
                StatAlias = param[0]
                FitsKey = param[1]
                FitsType = param[2]
                self.logger.info(f'loading header {param}')
                if FitsType == 'string':
                    FitsDefault = param[3]
                    FitsType = str
                elif FitsType == 'float':
                    FitsDefault = float(param[3])
                    FitsType = convertFloat
                elif FitsType == 'int':
                    FitsDefault = int(param[3])
                    FitsType = convertInt
                else:
                    raise TypeError('unknown fits card type: %s' % (FitsType))

                FitsComment = ""
                for i in range(len(param)):
                    if i >= 4:
                        if i == 4:
                            FitsComment = param[i]
                        else:
                            FitsComment = FitsComment + ' ' + param[i]

                StatAlias_list.append(StatAlias)
                FitsKey_list.append(FitsKey)
                FitsType_list.append(FitsType)
                FitsDefault_list.append(FitsDefault)
                FitsComment_list.append(FitsComment)
                header_num += 1

        header = dict(zip(FitsKey_list,
                          zip(StatAlias_list, FitsKey_list,
                              FitsType_list, FitsDefault_list, FitsComment_list)))

        fin.close()

        return header

    def init_stat_dict(self, header):
        statusDict = {}
        for name, card in header.items():
            if card[0] != 'NA':
                statusDict[card[0]] = card[3]
        return statusDict

    def initialize(self, ocsint):
        '''Initialize instrument.
        '''
        super(PFS, self).initialize(ocsint)
        self.logger.info('***** INITIALIZE CALLED *****')
        # Grab my handle to the OCS interface.
        self.ocs = ocsint

        # Get instrument configuration info
        self.obcpnum = self.ocs.get_obcpnum()
        self.insconfig = self.ocs.get_INSconfig()

        # Thread pool for autonomous tasks
        self.threadPool = self.ocs.threadPool

        # For task inheritance:
        self.tag = 'pfs'
        self.shares = ['logger', 'ev_quit', 'threadPool']

        # Get our 3 letter instrument code and full instrument name
        self.inscode = self.insconfig.getCodeByNumber(self.obcpnum)
        self.insname = self.insconfig.getNameByNumber(self.obcpnum)

        # Figure out our status table name.
        if self.obcpnum == 9:
            # Special case for SUKA.  Grrrrr!
            tblName1 = 'OBCPD'
        else:
            tblName1 = ('%3.3sS%04.4d' % (self.inscode, 1))

        self.keyTables = {}
        self.stattbl1 = self.ocs.addStatusTable(tblName1,
                                                ['status', 'mode', 'count',
                                                 'time'])

        self.registerStatusDict()

        # Add other tables here if you have more than one table...

        # Establish initial status values
        self.stattbl1.setvals(status='ALIVE', mode='LOCAL', count=0)

        # Handles to periodic tasks
        self.status_task = None
        self.power_task = None

        # Lock for handling mutual exclusion
        self.lock = threading.RLock()

    def registerStatusDict(self):
        """Let Gen2 know which status keys we are interested in. """

        rootDir = os.environ['ICS_GEN2ACTOR_DIR']
        self.tel_header = self.read_header_list(os.path.join(rootDir, "header_telescope.txt"))
        self.statusDictTel = self.init_stat_dict(self.tel_header)

    def start(self, wait=True):
        super(PFS, self).start(wait=wait)

        self.logger.info('PFS STARTED.')

        # Start auto-generation of status task
        t = common_task.IntervalTask(self.putstatus,
                                     self.param.status_interval)
        self.status_task = t
        t.init_and_start(self)

        self._reload()

        # Start task to monitor summit power.  Call self.power_off
        # when we've been running on UPS power for 60 seconds
        t = common_task.PowerMonTask(self, self.power_off, upstime=60.0)
        #self.power_task = t
        #t.init_and_start(self)

    def stop(self, wait=True):
        super(PFS, self).stop(wait=wait)

        # Terminate status generation task
        if self.status_task is not None:
            self.status_task.stop()

        self.status_task = None

        # Terminate power check task
        if self.power_task is not None:
            self.power_task.stop()

        self.power_task = None

        self.logger.info("PFS STOPPED.")


    #######################################
    # INTERNAL METHODS
    #######################################

    def execCmd(self, cmdStr, subtag=None, callback=None):
        self.logger.info('execIng: %s', cmdStr)
        proc = subprocess.Popen([cmdStr], shell=True, bufsize=1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ret = []
        while True:
            l = proc.stdout.readline()
            if not l or len(ret) > 1000:
                break
            ret.append(l.strip())

            self.ocs.setvals(subtag, cmd_str=l)

            if callback is not None:
                callback(subtag, l)

            if re.search('^\S+ \S+ [fF] .*', l):
                raise CamCommandError(l)

            self.logger.debug('exec ret: %s', l)

        err = proc.stderr.read()
        self.logger.warn('exec stderr: %s', err)

        self.logger.info('done with: %s', cmdStr)
        return ret

    def execOneCmd(self, actor, cmdStr, timelim=60.0, subtag=None, callback=None):
        """ Execute a command using the actorcore oneCmd wrapper.
        """

        return self.execCmd('oneCmd.py %s --level=i --timelim=%0.1f %s' % (actor, timelim, cmdStr),
                            subtag=subtag, callback=callback)

    def dispatchCommand(self, tag, cmdName, args, kwdargs):
        self.logger.debug("tag=%s cmdName=%s args=%s kwdargs=%s" % (
            tag, cmdName, str(args), str(kwdargs)))

        params = {}
        params.update(kwdargs)
        params['tag'] = tag

        try:
            # Try to look up the named method
            method = getattr(self, cmdName)

        except AttributeError as e:
            result = "ERROR: No such method in subsystem: %s" % (cmdName)
            self.logger.error(result)
            raise CamCommandError(result)

        return method(*args, **params)

    def update_header_stat(self):
        """ Update the external data feeding our headers. """

        self.logger.info('updating telescope info')
        self.ocs.requestOCSstatus(self.statusDictTel)

    def return_new_header(self, frameid, mode, itime, fullHeader=True, doUpdate=True):
        """ Update the external data feeding our headers and generate one. """

        if doUpdate:
            self.update_header_stat()

        self.logger.info('fetching header...')
        try:
            hdr = self.fetch_header(frameid, mode, itime,
                                    0.0,
                                    fullHeader=fullHeader)
        except Exception as e:
            self.logger.warn('failed to fetch header: %s', e)
            hdr = pyfits.Header()

        return base64.b64encode(hdr.tostring().encode('latin-1')).decode('latin-1')

    def fetch_header(self, frameid, mode, itime, utc_start,
                     fullHeader=True):

        hdr = pyfits.Header()

        if utc_start == 0.0:
            utc_start = datetime.utcnow()

        # Date and Time
        utc_end = utc_start + timedelta(seconds=float(itime))
        hst_start = utc_start - timedelta(hours=10)
        hst_end = hst_start + timedelta(seconds=float(itime))

        date_obs_str = utc_start.strftime('%Y-%m-%d')
        utc_start_str = utc_start.strftime('%H:%M:%S.%f')[:-3]
        utc_end_str = utc_end.strftime('%H:%M:%S.%f')[:-3]
        hst_start_str = hst_start.strftime('%H:%M:%S.%f')[:-3]
        hst_end_str = hst_end.strftime('%H:%M:%S.%f')[:-3]

        hdr.set('DATE-OBS',date_obs_str, "Observation start date")
        hdr.set('UT', utc_start_str, "[HMS] Typical UTC at exposure")
        hdr.set('UT-STR', utc_start_str, "[HMS] UTC at exposure start")
        hdr.set('UT-END', utc_end_str, "[HMS] UTC at exposure end")
        hdr.set('HST', hst_start_str, "[HMS] Typical HST at exposure")
        hdr.set('HST-STR', hst_start_str, "[HMS] HST at exposure start")
        hdr.set('HST-END', hst_end_str, "[HMS] HST at exposure end")

        # calculate MJD
        t_start = Time(utc_start, scale='utc')
        t_end = Time(utc_end, scale='utc')
        hdr.set('MJD',t_start.mjd, "Modified Julian Day at typical time")
        hdr.set('MJD-STR',t_start.mjd, "Modified Julian Day at exposure start")
        hdr.set('MJD-END',t_end.mjd, "Modified Julian Day at exposure end")

        # Local sidereal time
        # longitude = 155.4761
        # gmst = utcDatetime2gmst(utc_start)
        # lst = gmst2lst(longitude, hour=gmst.hour, minute=gmst.minute, second=(gmst.second + gmst.microsecond/10**6),
        #                lonDirection="W", lonUnits="Degrees")
        # lst_fmt = '%02d:%02d:%06.3f' % (lst[0],lst[1],lst[2])
        # hdr.set('LST',lst_fmt, "HH:MM:SS.SS typical LST at exposure")
        # hdr.set('LST-STR',lst_fmt, "HH:MM:SS.SS LST at exposure start")

        # gmst = utcDatetime2gmst(utc_end)
        # lst = gmst2lst(longitude, hour=gmst.hour, minute=gmst.minute, second=(gmst.second + gmst.microsecond/10**6),
        #                lonDirection="W", lonUnits="Degrees")
        # lst_fmt = '%02d:%02d:%06.3f' % (lst[0],lst[1],lst[2])
        # hdr.set('LST-END',lst_fmt, "HH:MM:SS.SS LST at exposure end")

        hdr.set('FRAMEID', frameid, "Image ID")
        if frameid.startswith('PFS'):
            try:
                framenum = frameid[4:]
                visit = int(framenum[:6], base=10)
            except:
                visit = 0
            hdr.set('EXP-ID', '%sE%06d00' % (frameid[:3], visit),
                    "Exposure/visit ID")
            hdr.set('W_VISIT', visit, 'PFS visit')

        # exposure time
        hdr.set('EXPTIME',float(itime), "[sec] Total integration time of the frame")
        hdr.set('DATA-TYP', mode.upper(), "Subaru-style exp. type")

        if fullHeader is False:
            return hdr

        # Telescope header
        for name, hdr1 in self.tel_header.items():
            name = hdr1[1]
            comment = hdr1[4]
            if hdr1[0] == 'NA':
                hdr.set(name, hdr1[3], comment)
            else:
                val = self.statusDictTel[hdr1[0]]
                valType = hdr1[2]
                try:
                    val = valType(val)
                except:
                    hdr.add_comment(f'FAILED to convert {name}:{val} as a {valType}')

                hdr.set(name, val, comment)

        return hdr

    #######################################
    # INSTRUMENT COMMANDS
    #######################################

    def obcp_mode(self, motor='OFF', mode=None, tag=None):
        """One of the commands that are in the SOSSALL.cd
        """
        self.mode = mode

    def _subtag(self, tag):
        """ Convert a tag into a subtag. """

        # extend the tag to make a subtag
        subtag = '%s.1' % tag

        # Set up the association of the subtag in relation to the tag
        # This is used by integgui to set up the subcommand tracking
        # Use the subtag after this--DO NOT REPORT ON THE ORIGINAL TAG!
        self.ocs.setvals(tag, subpath=subtag)

        return subtag

    def sleep(self, tag=None, sleep_time=0):

        itime = float(sleep_time)

        subtag = self._subtag(tag)

        # Report on a subcommand.  Interesting tags are:
        # * Having the value of float (e.g. time.time()):
        #     task_start, task_end
        #     cmd_time, ack_time, end_time (for communicating systems)
        # * Having the value of str:
        #     cmd_str, task_error

        self.ocs.setvals(subtag, task_start=time.time(),
                         cmd_str='Sleep %f ...' % itime)

        self.logger.info("\nSleeping for %f sec..." % itime)
        while int(itime) > 0:
            self.ocs.setvals(subtag, cmd_str='Sleep %f ...' % itime)
            sleep_time = min(1.0, itime)
            time.sleep(sleep_time)
            itime -= 1.0

        self.ocs.setvals(subtag, cmd_str='Awake!')
        self.logger.info("Woke up refreshed!")
        self.ocs.setvals(subtag, task_end=time.time())

    def _reload(self, subtag=None, module=None):
        self.logger.info("Reloading %s", module)

        import PFSCommands
        reload(PFSCommands)

        for n in PFSCommands.__all__:
            if subtag is not None:
                self.ocs.setvals(subtag, cmd_str=f'Trying to reload {n}\n')
            self.logger.info("Reloading %s.%s", module, n)
            setattr(self, n, getattr(PFSCommands, n).__get__(self))
            if subtag is not None:
                self.ocs.setvals(subtag, cmd_str=f'reloaded {n}\n')
            self.logger.info("Reloaded %s.%s", module, n)

        self.logger.info("Reloaded all of %s", module)

    def reload(self, tag=None, module=None):
        """ Reload some or all Gen2 commands. """

        subtag = self._subtag(tag)
        self.ocs.setvals(subtag, task_start=time.time(),
                         cmd_str=f'Reloading {module} ...')
        self._reload(subtag=subtag, module=module)
        self.ocs.setvals(subtag, task_end=time.time())

    def fits_file(self, motor='OFF', frame_no=None, target=None, template=None, delay=0,
                  tag=None):
        """One of the commands that are in the SOSSALL.cd.
        """

        self.logger.info("fits_file called...")

        if not frame_no:
            return 1

        # TODO: make this return multiple fits files
        if ':' in frame_no:
            (frame_no, num_frames) = frame_no.split(':')
            num_frames = int(num_frames)
        else:
            num_frames = 1

        # Check frame_no
        match = re.match('^(\w{3})(\w)(\d{8})$', frame_no)
        if not match:
            raise PFSError("Error in frame_no: '%s'" % frame_no)

        inst_code = match.group(1)
        frame_type = match.group(2)
        # Convert number to an integer
        try:
            frame_cnt = int(match.group(3))
        except ValueError as e:
            raise PFSError("Error in frame_no: '%s'" % frame_no)

        statusDict = {
            'FITS.PFS.PROP-ID': 'None',
            'FITS.PFS.OBSERVER': 'None',
            'FITS.PFS.OBJECT': 'None',
        }
        try:
            res = self.ocs.requestOCSstatus(statusDict)
            self.logger.debug("Status returned: %s" % (str(res)))

        except PFSError as e:
            return (1, "Failed to fetch status: %s" % (str(e)))

        # Iterate over number of frames, creating fits files
        frame_end = frame_cnt + num_frames
        framelist = []

        while frame_cnt < frame_end:
            # Construct frame_no and fits file
            frame_no = '%3.3s%1.1s%08.8d' % (inst_code, frame_type, frame_cnt)
            if template is None:
                fits_f = pyfits.HDUList(pyfits.PrimaryHDU())
            else:
                templfile = os.path.abspath(template)
                if not os.path.exists(templfile):
                    raise PFSError("File does not exist: %s" % (templfile))

                fits_f = pyfits.open(templfile)

            hdu = fits_f[0]
            updDict = {'FRAMEID': frame_no,
                       'EXP-ID': frame_no,
                       }

            self.logger.info("updating header")
            for key, val in list(updDict.items()):
                hdu.header.update(key, val)

            subaruCards = self.return_new_header(frame_cnt, 'blank', 0.0)
            hdu.header.extend(subaruCards)

            fitsfile = '/tmp/%s.fits' % frame_no
            try:
                os.remove(fitsfile)
            except OSError:
                pass
            fits_f.writeto(fitsfile, output_verify='ignore')
            fits_f.close()

            # Add it to framelist
            framelist.append((frame_no, fitsfile))

            frame_cnt += 1

        # self.logger.debug("done exposing...")

        # If there was a non-negligible delay specified, then queue up
        # a task for later archiving of the file and terminate this command.
        if delay:
            if isinstance(delay, str):
                delay = float(delay)
            if delay > 0.1:
                # Add a task to delay and then archive_framelist
                self.logger.info("Adding delay task with '%s'" % 
                                 str(framelist))
                t = common_task.DelayedSendTask(self.ocs, delay, framelist)
                t.initialize(self)
                self.threadPool.addTask(t)
                return 0

        # If no delay specified, then just try to archive the file
        # before terminating the command.
        self.logger.info("Submitting framelist '%s'" % str(framelist))
        self.ocs.archive_framelist(framelist)

    def putstatus(self, target="ALL"):
        """Forced export of our status.
        """
        # Bump our status send count and time
        self.stattbl1.count += 1
        self.stattbl1.time = time.strftime("%4Y%2m%2d %2H%2M%2S",
                                           time.localtime())

        self.ocs.exportStatus()

    def getstatus(self, target="ALL"):
        """Forced import of our status using the normal status interface.
        """
        ra, dec, focusinfo, focusinfo2 = self.ocs.requestOCSstatusList2List(['STATS.RA',
                                                                             'STATS.DEC',
                                                                             'TSCV.FOCUSINFO',
                                                                             'TSCV.FOCUSINFO2'])

        self.logger.info("Status returned: ra=%s dec=%s focusinfo=%s focusinfo2=%s" % (ra, dec,
                                                                                       focusinfo,
                                                                                       focusinfo2))

    def getstatus2(self, target="ALL"):
        """Forced import of our status using the 'fast' status interface.
        """
        ra, dec = self.ocs.getOCSstatusList2List(['STATS.RA',
                                                  'STATS.DEC'])

        self.logger.info("Status returned: ra=%s dec=%s" % (ra, dec))

    def view_file(self, path=None, num_hdu=0, tag=None):
        """View a FITS file in the OCS viewer.
        """
        self.ocs.view_file(path, num_hdu=num_hdu)


    def view_fits(self, path=None, num_hdu=0, tag=None):
        """View a FITS file in the OCS viewer
             (sending entire file as buffer, no need for astropy).
        """
        self.ocs.view_file_as_buffer(path, num_hdu=num_hdu)


    def reqframes(self, num=1, type="A"):
        """Forced frame request.
        """

        self.logger.info('reqframes num=%r type=%r', num, type)
        framelist = self.ocs.getFrames(num, type)

        # This request is not logged over DAQ logs
        self.logger.info("framelist: %s" % str(framelist))

        return framelist

    def kablooie(self, motor='OFF'):
        """Generate an exception no matter what.
        """
        raise PFSError("KA-BLOOIE!!!")


    def defaultCommand(self, *args, **kwdargs):
        """This method is called if there is no matching method for the
        command defined.
        """

        # If defaultCommand is called, the cmdName is pushed back on the
        # argument tuple as the first arg
        cmdName = args[0]
        self.logger.info("Called with command '%s', params=%s" % (cmdName,
                                                                  str(kwdargs)))

        res = unimplemented_res
        self.logger.info("Result is %d\n" % res)

        return res

    def power_off(self, upstime=None):
        """
        This method is called when the summit has been running on UPS
        power for a while and power has not been restored.  Effect an
        orderly shutdown.  upstime will be given the floating point time
        of when the power went out.
        """
        res = 1
        try:
            self.logger.info("!!! POWERING DOWN !!!")
            # res = os.system('/usr/sbin/shutdown -h 60')

        except OSError as e:
            self.logger.error("Error issuing shutdown: %s" % str(e))

        self.stop()

        self.ocs.shutdown(res)
