import functools
import os
import time

from g2cam.Instrument import CamCommandError
from opscore.actor.keyvar import AllCodes, DoneCodes

__all__ = ['pfsDribble',
           '_runPfsCmd',
           'keyFromReply',
           'pfscmd',
           'mcsexpose',
           'getPfsVisit',
           'archivePfsFile',
           '_frameToVisit']

def _runPfsCmd(self, actor, cmdStr, tag, timeLim=30.0, callFunc=None):
    """ Run one MHS command, and report back to tag.

    Args
    ----
    actor : str
      The MHS actor name
    cmdStr : str
      The actor command string
    tag : ?
      The thing which we tag our OCS responses with.
    callFunc : callable
      If set, a function which is called with each reply line from the command.
      Called as callFunc(replyLine, tag=tag)
    """

    self.logger.info(f'dispatching MHS command: {actor} {cmdStr}')
    if callFunc is None:
        ret = self.actor.cmdr.call(actor=actor,
                                   cmdStr=cmdStr,
                                   timeLim=timeLim)
        if ret.didFail:
            raise CamCommandError(f'actor {actor} command {cmdStr} failed')
        return ret
    else:
        callFunc = functools.partial(callFunc, tag=tag)
        self.actor.cmdr.bgCall(actor=actor,
                               cmdStr=cmdStr,
                               timeLim=timeLim,
                               callCodes=AllCodes,
                               callFunc=callFunc)
        return None

def pfsDribble(self, reply, tag=None):
    """ Utility callFunc which should forward each replyLine to the OCS. """

    self.logger.info(f'reply: {reply}, line: {reply.lastReply}')

    if reply.didFail:
        raise CamCommandError(f'fail: {reply}')
    if reply.isDone:
        self.ocs.setvals(tag, task_end=time.time(),
                         cmd_str=f'OK')
        return
    self.ocs.setvals(tag, cmd_str=str(reply.lastReply))
    time.sleep(0.1)

def pfscmd(self, tag=None, actor=None, cmd=None, callFunc=None, keyVars=None):
    """ Send an arbitrary command to an arbitrary actor.

    Args
    ----
    tag : object
      The OCS tag. Set by the g2cam dispatcher.
    actor : str
      The MHS actor name
    cmd : str
      The actor command string.
    callFunc : callable
      If set, called with each reply from the command.
      Called as callFunc(replyLine, tag=tag)
    keyVars : iterable
      A list of keyVars to keep and return

    Notes
    -----
    This can be called from the Gen2 EXEC dispatcher. In that case, callFunc will always be None
"""

    subtag = self._subtag(tag)

    if callFunc is True:
        callFunc = self.pfsDribble

    self.ocs.setvals(subtag, task_start=time.time(),
                     cmd_str=f'calling {actor} {cmd} ...')
    ret = self._runPfsCmd(actor, cmd, subtag, callFunc=callFunc)

    if callFunc is None:
        lines = [str(l) for l in ret.replyList]
        self.ocs.setvals(subtag,
                         cmd_str='\n'.join(lines))
    return ret

def keyFromReply(self, cmdReply, keyName):
    for l in cmdReply.replyList:
        for k in l.keywords:
            if keyName == k.name:
                return k

def mcsexpose(self, tag=None, exptype='bias', exptime=0.0, docentroid='FALSE'):
    exptype = exptype.lower()
    docentroid = docentroid.lower()
    exptime = float(exptime)

    subtag = self._subtag(tag)

    self.ocs.setvals(subtag, task_start=time.time(),
                     cmd_str=f'Starting {exptype} exposure')

    if exptype in {'object', 'test'} and docentroid == 'true':
        doCentroidArg = "doCentroid"
    else:
        doCentroidArg = ""

    if exptype == 'bias':
        ret = self.pfscmd(tag=tag, actor='mcs',
                          cmd='expose bias')
    else:
        ret = self.pfscmd(tag=tag, actor='mcs',
                          cmd=f'expose {exptype} expTime={exptime} {doCentroidArg}')

    filename = self.keyFromReply(ret, 'filename').values[0]
    self.logger.warn('########### filename: %s' % filename)

    self.archivePfsFile(filename)

    self.ocs.setvals(subtag, cmd_str="Finished MCS exposure", task_end=time.time())

def _frameToVisit(self, frame):
    return int(frame[4:4+6], base=10), int(frame[10:12], base=10)

def getPfsVisit(self):
    """ Return a PFS visit ID, wrapping the standard .reqframes() """

    frame = self.reqframes(num=100)[0]
    visit, rest = self._frameToVisit(frame)

    return visit

def archivePfsFile(self, pathname):
    filename = os.path.basename(pathname)
    fullframeId = os.path.splitext(filename)[0]
    frameId = fullframeId

    framelist = [(frameId, pathname)]
    self.logger.info('archiving: %s' % (framelist))

    self.ocs.archive_framelist(framelist)
