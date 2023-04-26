#!/usr/bin/env python

from datetime import datetime
import sys

# Print long reqframes requests. Feed with the Gen2-side logs on stdin.
# e.g. cat /data/logs/actors/gen2/2023*_*.log | frameTimes.py
#
def run():
    t0 = None
    while True:
        l = sys.stdin.readline()
        if not l:
            break
        if '(reqframes)' not in l:
            continue

        ts, rest = l.split('|', 1)
        ts = ts.strip()
        try:
            t = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S,%f')
        except Exception as e:
            print(f'failed to parse date: {ts}')
            raise

        if 'reqframes num=' in l:
            t0 = t
        else:
            t1 = t
            dt = (t1-t0).total_seconds()

            if dt > 5:
                print(f'{t0.strftime("%Y-%m-%d %H:%M:%S")} {dt}')

if __name__ == "__main__":
    run()
    
        
        
