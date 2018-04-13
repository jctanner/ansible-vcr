#!/usr/bin/env python

# EXPANDER
#
#   Take the fixtures created by an ansible-vcr recording and expand them
#   to a new arbitary hostcount.

import argparse
import glob
import os
import shutil
import subprocess
import sys


def run_command(cmd):
    p = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    so, se = p.communicate()
    return (p.returncode, so, se)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixturedir', default='/tmp/fixtures')
    parser.add_argument('--hostcount', type=int)
    args = parser.parse_args()

    taskdirs = glob.glob('%s/*' % args.fixturedir)
    taskdirs = [x for x in taskdirs if x[-1].isdigit()]

    hostdirs = []
    for td in taskdirs:
        hostdirs += glob.glob('%s/*' % td)

    hosts = set()
    for hd in hostdirs:
        hostname = hd.split('/')[-1]
        hosts.add(hostname)

    counter = len(hosts) + 1
    while len(hosts) < args.hostcount:
        _hosts = [x for x in hosts]
        hn = _hosts[-1]
        hn_parts = hn.split('.')
        sn = hn_parts[0]
        sn += str(counter)
        hn_parts[0] = sn
        nn = '.'.join(hn_parts)
        if nn not in hosts:
            hosts.add('.'.join(hn_parts))
        counter += 1

    for td in taskdirs:
        src = [x for x in hostdirs if x.startswith(td + '/')][-1]
        src_hn = src.split('/')[-1]
        for hn in hosts:
            hdir = os.path.join(td, hn)
            if not os.path.isdir(hdir):
                shutil.copytree(src, hdir)
            hdir_files = glob.glob('%s/*' % hdir)
            for hdf in hdir_files:
                cmd = "sed -i.bak 's/%s/%s/g' %s" % (src_hn, hn, hdf)
                (rc, so, se) = run_command(cmd)


if __name__ == "__main__":
    main()
