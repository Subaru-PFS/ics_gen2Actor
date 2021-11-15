#!/usr/local/bin/env python

import actorcore.Actor

class OurActor(actorcore.Actor.Actor):
    def __init__(self, name,
                 productName=None, configFile=None,
                 modelNames=('gen2','iic','mcs','fps','agcc','dcb'),
                 debugLevel=30):

        """ Setup an Actor instance. See help for actorcore.Actor for details. """
        
        # This sets up the connections to/from the hub, the logger, and the twisted reactor.
        #
        actorcore.Actor.Actor.__init__(self, name, 
                                       productName=productName, 
                                       configFile=configFile,
                                       modelNames=modelNames)

        self.everConnected = False

    def connectionMade(self):
        if self.everConnected:
            return
        self.everConnected = True

        models = []
        for sm in 1,2,3,4:
            for arm in 'b','r','n':
                models.append(f'ccd_{arm}{sm}')
        self.addModels(models)

#
# To work
def main():
    theActor = OurActor('gen2', productName='gen2Actor')
    return theActor
    # theActor.run()

if __name__ == '__main__':
    main()
