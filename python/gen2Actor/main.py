#!/usr/local/bin/env python

import actorcore.Actor

class OurActor(actorcore.Actor.Actor):
    def __init__(self, name,
                 productName=None, configFile=None,
                 modelNames=('gen2','mcs','iic','fps'),
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

        fpsModel = self.models['fps'].keyVarDict
        fpsModel['mcsBoresight'].addCallback(self.gen2.newMcsBoresight, callNow=False)

#
# To work
def main():
    theActor = OurActor('gen2', productName='gen2Actor')
    return theActor
    # theActor.run()

if __name__ == '__main__':
    main()
