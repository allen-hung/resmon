#!/usr/bin/python

import os
import sys
import struct
import binascii
import socket
import select
import glob
from resmon.common import admin_dir, command_magic_word, payload_to_packet, PacketPool, reply_magic_word
from resmon.command import Command
from resmon.config import id_regex

"""
    Command packet:
    [MW][LEN][PL][CRC]

    MW:  2 bytes, Magic Word, 02h b7h
    LEN: 2 bytes, the total length of command packet in bytes decreased by 1
    PL:  0~N bytes, arbitrary payload data
    CRC: 4 bytes, the CRC of command packet
"""

program_name = "resmon-cli"

usage = """{0}: command line interface to interact with the running resmon daemons

Usage: {0} show  [profile | profile:resource]
       {0} start profile:resource
       {0} stop  [profile | profile:resource]
       {0} help | --help | -h

       show
            show the status of all running daemons, the daemon of which name is
            specified, or the resource of which name is specified.

       start
            start the resource of which name is specified

       stop
            stop all all running daemons, the daemon of which name is specified,
            or the resource of which name is specified.

       help
            show this help
""".format(program_name)

def print_error(*args):
    msg = " ".join([str(arg) for arg in args])
    print >>sys.stderr, "\033[91m{}\033[0m".format(msg)

def print_usage(error=None):
    if error:
        print_error("Usage error:", error)
        print_error("")
    print usage
    sys.exit(1 if error else 0) 

def to_payload(command, data=""):
    return struct.pack("H", command) + data

def issue_profile_command(profile, command, data=""):
    domain_name = admin_dir + "/profile-{}.sock".format(profile)
    return issue_command(domain_name, command, data)

def issue_command(domain_name, command, data=""):
    #domain_name = admin_dir + "/profile-{}.sock".format(profile)
    payload = to_payload(command, data)
    packet = payload_to_packet(command_magic_word, payload)
    my_addr = admin_dir + "/cli-{}.sock".format(binascii.b2a_hex(os.urandom(4)))
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(my_addr)
        sock.connect(domain_name)
    except socket.error, msg:
        if os.path.exists(my_addr):
            os.unlink(my_addr)
        raise RuntimeError("failed to connect: " + msg)

    pool = PacketPool(reply_magic_word)
    reply = ""
    try:
        sock.sendall(packet)
        inputs = [sock]
        while True:
            readable, writable, exceptional = select.select(inputs, [], [])
            if sock in readable:
                data = sock.recv(8192)
                if len(data) == 0:
                    break # socket connection broken
                pool.feed_buffer(data)
                if pool.has_payload():
                    break
            else:
                break
        payload = pool.get_payload()
        while payload:
            reply += payload
            payload = pool.get_payload()
        os.remove(my_addr)
    except Exception as e:
        print_error("internal error:", e)
    finally:
        sock.close()
    return reply

def print_reply(data):
    if data:
        if len(data) > 0 and data[-1] == '\n': data = data[:-1]
        print data

def show_profile(name):
    try:
        reply = issue_profile_command(name, Command.SHOW_PROFILE)
        print_reply(reply)
    except Execption as e:
        print_error(e)

def show_resource(name):
    index = name.find(':')
    profile = name[:index]
    try:
        reply = issue_profile_command(profile, Command.SHOW_RESOURCE, name)
        print_reply(reply)
    except Execption as e:
        print_error(e)

def show_all_profiles():
    profiles = glob.glob("/var/run/resmon/profile-*.sock")
    replies = []
    for p in profiles:
        try:
            replies.append(issue_command(p, Command.SHOW_PROFILE))
        except:
            pass

    if len(replies) == 0:
        print "No resmond process is found"
    else:
        for reply in replies:
            if reply is not replies[0]: print
            print_reply(reply)

def start_resource(name):
    index = name.find(':')
    profile = name[:index]
    try:
        reply = issue_profile_command(profile, Command.START_RESOURCE, name)
        print_reply(reply)
    except Execption as e:
        print_error(e)

def stop_profile(name):
    print "stop profile: is not implemented"

def stop_resource(name):
    index = name.find(':')
    profile = name[:index]
    try:
        reply = issue_profile_command(profile, Command.STOP_RESOURCE, name)
        print_reply(reply)
    except Execption as e:
        print_error(e)

def stop_all_profiles():
    print "stop all profiles is not implemented"

def is_profile_name(str):
    return id_regex.match(str)

def is_resource_name(str):
    index = str.find(':')
    return index >= 0 and id_regex.match(str[:index]) and id_regex.match(str[index+1:])

def parsing_args():
    argv = sys.argv[1:]
    if len(argv) == 0:
        print_usage()

    cmd = argv[0]
    argv = argv[1:]
    if cmd == "show":
        if len(argv) == 0:
            show_all_profiles()
        elif len(argv) > 1:
            print_usage("too many options for '{}'".format(cmd))
        elif is_profile_name(argv[0]):
            show_profile(argv[0])
        elif is_resource_name(argv[0]):
            show_resource(argv[0])
        else:
            print_usage("invalid name for '{}'".format(cmd))
    elif cmd == "start":
        if len(argv) == 0:
            print_usage("'{}' needs one option for resource name".format(cmd))
        if len(argv) > 1:
            print_usage("too many options for '{}'".format(cmd))
        if is_resource_name(argv[0]):
            start_resource(argv[0])
        else:
            print_usage("invalid name for '{}'".format(cmd))
    elif cmd == "stop":
        if len(argv) == 0:
            stop_all_profiles()
        elif len(argv) > 1:
            print_usage("too many options for '{}'".format(cmd))
        elif is_profile_name(argv[0]):
            stop_profile(argv[0])
        elif is_resource_name(argv[0]):
            stop_resource(argv[0])
        else:
            print_usage("invalid name for '{}'".format(cmd))
    elif cmd == "help" or cmd == "--help" or cmd == "-h":
        if len(argv) > 0:
            print_usage("invalid options for '{}'".format(cmd))
        else:
            print_usage()
    else:
        print_usage("unknown command: {}".format(cmd))

def main():
    global program_name
    config_filename = parsing_args()

if __name__ == "__main__":
    main()
