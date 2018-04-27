#!/usr/bin/python

import os
import sys
import config
from log import LogFatal
from daemon import Daemon
from config import load_config

program_name = "resmond"

def print_error(msg):
    sys.stderr.write("\033[91m%s\033[0m\n" % msg)

def print_warn(msg):
    sys.stderr.write("\033[93m%s\033[0m\n" % msg)

def print_usage(error=None):
    if error:
        print_error("Error: {}".format(error))
        print
    print "Usage: {0} [OPTION] [CONFIG_FILE]".format(program_name)
    print ""
    print "Options:"
    print "  -h, --help    Show this help"
    print ""
    sys.exit(1 if error else 0) 

def parsing_args():
    if len(sys.argv) == 1:
        print_usage()

    accept_flags = True
    flag_help = False
    filename = None
    for arg in sys.argv[1:]:
        if (len(arg) >= 1 and arg[:1] == "-") or (len(arg) >= 2 and arg[:2] == "--"):
            if not accept_flags:
                print_usage("Options are not legal after filename: {}".format(arg))
            if arg == "--help" or arg == "-h":
                flag_help = True;
            else:
                print_usage("Unknown option: {}".format(arg))
        else:
            accept_flags = False
            if not filename:
                filename = arg
            else:
                print_usage("Too many filenames!")

    if flag_help and filename:
        print_usage("Filename cannot be specified with the options: -h, --help" )

    if flag_help:
        print_usage()

    return filename

def main():
    global program_name
    program_name = os.path.split(sys.argv[0])[1]
    config_filename = parsing_args()
    profile = load_config(config_filename)
    if len(profile.resources) == 0:
        print_warn("No resource specified in profile '{}', process is stopped!".format(profile.name))
        sys.exit(0)
    Daemon(profile).start()
