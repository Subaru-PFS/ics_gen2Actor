The Gen2 actor
--------------

The Gen2 actor is the only program which handles communications
between the Subaru OCS world and the PFS MHS world. It accepts
OCS commands from the Subaru observing interface and MHS commands from
MHS actors.

From the point of view of the Subaru observing system, it implements
the commands in the `kansoku` `PFS` directory.

From the point of view of the PFS instrument, it mostly provides
Gen2 frame IDs, and the FITS cards for the telescope and observatory.

