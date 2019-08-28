#!/usr/local/bin/env python

import actorcore.Actor

class OurActor(actorcore.Actor.Actor):
    def __init__(self, name,
                 productName=None, configFile=None,
                 modelNames=('gen2','mcs','iic'),
                 debugLevel=30):

        """ Setup an Actor instance. See help for actorcore.Actor for details. """
        
        # This sets up the connections to/from the hub, the logger, and the twisted reactor.
        #
        actorcore.Actor.Actor.__init__(self, name, 
                                       productName=productName, 
                                       configFile=configFile,
                                       modelNames=modelNames)

    def connectionMade(self):
        mcsModel = self.models['mcs'].keyVarDict
        mcsModel['filename'].addCallback(self.gen2.newFilePath, callNow=False)

    def _getGen2Key(self, cmd, name):
        """ Utility to wrap fetching Gen2 keyword values. 

        Bugs
        ----

        This is disgustingly intimate with the current PFS.py internals. Those will change.
        """

        hdr1 = self.gen2.tel_header[name]

        val = self.gen2.statusDictTel[hdr1[0]]
        valType = hdr1[2]
        try:
            val = valType(val)
        except:
            cmd.warn(f'text="FAILED to convert {name}:{val} as a {valType}"')

        return val

    def _genActorKeys(self, cmd, doGen2Refresh=True):
        """Generate all gen2 status keys.

        For this actor, this might get called from either the gen2 or the MHS sides.

        Bugs
        ---

        With the current Gen2 keyword table implementation, we do not
        correctly set invalid values to the right type. I think the
        entire mechanism will be changed.

        """

        def gk(name, cmd=cmd):
            return self._getGen2Key(cmd, name)

        if doGen2Refresh:
            self.gen2.update_header_stat()
        cmd.inform(f'tel_focus={gk("TELFOCUS")},{gk("FOC-VAL")}')
        cmd.inform(f'tel_axes={gk("AZIMUTH")},{gk("ALTITUDE")}')
        cmd.inform(f'tel_rot={gk("INST-PA")},{gk("INR-STR")}')
        cmd.inform(f'tel_adc={gk("ADC-TYPE")},{gk("ADC-STR")}')
        cmd.inform(f'dome_env={gk("DOM-HUM")},{gk("DOM-PRS")},{gk("DOM-TMP")},{gk("DOM-WND")}')
        cmd.inform(f'outside_env={gk("OUT-HUM")},{gk("OUT-PRS")},{gk("OUT-TMP")},{gk("OUT-WND")}')


#
# To work
def main():
    theActor = OurActor('gen2', productName='gen2Actor')
    return theActor
    # theActor.run()

if __name__ == '__main__':
    main()
