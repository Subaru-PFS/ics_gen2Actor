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
           '_frameToVisit',
           '_getNextVisit']

def _runPfsCmd(self, actor, cmdStr, tag, timeLim=10.0, callFunc=None):
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
    time.sleep(0.5)

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

    if False and callFunc is None:
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

def mcsexpose(self, tag=None, exptype='bias', exptime=0.0):

    exptime = float(exptime)

    subtag = self._subtag(tag)

    self.ocs.setvals(subtag, task_start=time.time(),
                     cmd_str=f'Starting {exptype} exposure')

    if exptype == 'bias':
        ret = self.pfscmd(tag=tag, actor='mcs',
                          cmd='expose bias')
    else:
        ret = self.pfscmd(tag=tag, actor='mcs',
                          cmd=f'expose {exptype} expTime={exptime}')

    filename = self.keyFromReply(ret, 'filename').values[0]
    self.logger.warn('########### filename: %s' % filename)

    self.archivePfsFile(filename)

    self.ocs.setvals(subtag, cmd_str="Finished MCS exposure", task_end=time.time())

def _frameToVisit(self, frame):
    return int(frame[4:4+6], base=10), int(frame[10:12], base=10)

def _getNextVisit(self, camId):
    """ For the given camId, get the next proper PFS visit, where the Gen2 frame%100 == 0 """

    frame = self.reqframes(num=1, type=camId)[0]
    visit, rest = self._frameToVisit(frame)

    # If our frame # does not end with 00, request up to the next one which does
    if rest != 0:
        fillIn = self.reqframes(num=99-rest+1, type=camId)
        self.logger.warn(f'frame for {camId} is not %100==0 {frame}. Catching up to {fillIn[-1]}')
        frame = fillIn[-1]
        visit, rest = self._frameToVisit(frame)
        if rest != 0:
            raise RuntimeError(f'frame catchup to 100 for {camId} botch: {frame}')

    fillIn = self.reqframes(num=99, type=camId)
    visit2, rest2 = self._frameToVisit(fillIn[-1])
    if visit != visit2 or rest2 != 99:
        raise RuntimeError(f'frame discard to 100 for {camId} botch: {frame} vs {fillIn[-1]}')

    return visit

def getPfsVisit(self):
    """ Return a PFS visit ID, wrapping the standard .reqframes()

    reqframes() returns N filename(s) for a given prefix ("PFSA")
    We have four prefixes (PFS[ABCD]) and always use frames by the 100.

    That will eventually be handled by some Gen2 wrapper. For now, do it ourselves.
    """

    camIds = 'A', 'B', 'C', 'D'

    visits = {}
    for camId in camIds:
        visits[camId] = self._getNextVisit(camId)

    # Now make sure that all camIds have the same visit. If not,
    # request and discard chunks of 100 until all match.
    if not all([visits[c] == visits[camIds[0]] for c in camIds[1:]]):
        ordered = [(camId, visits[camId]) for camId in sorted(visits, key=visits.get, reverse=True)]
        camToMatch, visitToMatch = ordered[0]
        for camId, visit in ordered[1:]:
            for i in range(visitToMatch-visit):
                self.logger.warn(f'bumping {camId} {visit} to match {camToMatch}, {visitToMatch}')
                visits[camId] = self._getNextVisit(camId)

    return visits[camIds[0]]

def archivePfsFile(self, pathname):
    filename = os.path.basename(pathname)
    fullframeId = os.path.splitext(filename)[0]
    frameId = fullframeId

    framelist = [(frameId, pathname)]
    self.logger.info('archiving: %s' % (framelist))

    self.ocs.archive_framelist(framelist)
