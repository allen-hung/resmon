import os
import threading
import binascii
import struct
import traceback
from resource import admin_dir
from log import LogDebug, LogInfo, LogError, LogFatal
from common import _enum_, command_magic_word, SocketServer, PacketPool, reply_magic_word, payload_to_packet
from resource import State, ResourceState

Command = _enum_(
    "SHOW_PROFILE",
    "SHOW_RESOURCE",
    "START_PROFILE",
    "START_RESOURCE",
    "STOP_PROFILE",
    "STOP_RESOURCE",
)

class CommandProcessor(threading.Thread):
    def __init__(self, daemon):
        super(CommandProcessor, self).__init__(name="command processor")
        self.daemon = daemon
        self.profile = daemon.profile
        self.packet_pool = PacketPool(command_magic_word)
        self.socket_server = None

    def log_error(self, *args):
        LogError("[{}] ".format(self.profile.name), *args)

    def log_debug(self, *args):
        LogDebug("[{}] ".format(self.profile.name), *args)

    def log_info(self, *args):
        LogInfo("[{}] ".format(self.profile.name), *args)

    def do_show_profile(self):
        reply = "Profile name: {}\n".format(self.profile.name)
        reply += "Resources:\n"
        for res in self.daemon.resources:
            state = ResourceState.rev_map[res.res_state]
            action = ""
            if res.state is State.AUTOSTART:
                action = ", being atuto-started"
            elif res.state is State.RECOVER:
                action = ", under recovery"
            elif res.state is State.MONITOR:
                action = ", under monitoring"
            head = "  [" + res.name + "] "
            pad = " " * (30-len(head)) if len(head) < 30 else ""
            reply += "{}{}{}{}\n".format(head, pad, state, action)
        return reply

    def do_start_resource(self, name):
        found = [r for r in self.daemon.resources if r.name == name]
        if len(found) == 0:
            return "no such resource"

        res = found[0]
        if res.res_state == ResourceState.STARTED:
            return "{} is already started".format(name)

        if res.state not in [State.START, State.AUTOSTART, State.RECOVER, State.MONITOR]:
            res.info("resource is to be started by command")
            res.state = State.START
        return "ok"

    def do_stop_resource(self, name):
        found = [r for r in self.daemon.resources if r.name == name]
        if len(found) == 0:
            return "no such resource"

        res = found[0]
        if res.res_state == ResourceState.STOPPED:
            return "{} is already stopped".format(name)

        res.info("resource is to be stopped by command")
        res.state = State.STOP
        return "ok"

    def do_show_resource(self, name):
        found = [r for r in self.daemon.resources if r.name == name]
        if len(found) == 0:
            return "no such resource"

        res = found[0]

        reply =  "Profile name:  {}\n".format(self.profile.name)
        reply += "Resource name: {}\n".format(res.name)
        reply += "    State:  {}\n".format(ResourceState.rev_map[res.res_state])
        action = ""
        if res.state is State.AUTOSTART:
            action = " (do atuto-starting)"
        elif res.state is State.RECOVER:
            action = " (do recovery)"
        elif res.state is State.MONITOR:
            action = " (do monitoring)"
        reply += "    Daemon: {}{}\n".format(State.rev_map[res.state], action)
        reply += "    Events:\n"
        """ grep the log file """
        try:
            filename = self.profile.logfile.name
            log = open(filename, "r")
            current_session = "[{}]: ".format(os.getpid())
            current_session = "[29939]: "
            resource_name = "[{}] ".format(res.name)
            debug_pattern = current_session + "<debug>"
            for line in log:
                if current_session not in line:
                    continue
                if resource_name not in line:
                    continue
                if debug_pattern in line:
                    continue
                
                index = line.find(current_session)
                line = line[:index] + line[index+len(current_session):]
                index = line.find(resource_name)
                line = line[:index] + line[index+len(resource_name):]
                reply += "    " + line
        except Exception as e:
            reply += "unable to open '{}': {}\n".format(filename, e)
        return reply

    def do_command(self, payload):
        reply = "Internal error!\n"
        if len(payload) < 2:
            self.log_error("invalid payload: too small")
        else:
            command = struct.unpack("H", payload[0:2])[0]
            data = payload[2:]
            if command == Command.SHOW_PROFILE:
                reply = self.do_show_profile()
            elif command == Command.START_RESOURCE:
                reply = self.do_start_resource(data)
            elif command == Command.SHOW_RESOURCE:
                reply = self.do_show_resource(data)
            elif command in Command.rev_map:
                self.log_error("unsupported command: ", Command.rev_map[command])
            else:
                self.log_error("unknown command: ", command)
        return reply

    def socket_server_process_data(self, data):
        self.packet_pool.feed_buffer(data)
        payload = self.packet_pool.get_payload()
        reply = ""
        while payload:
            data = self.do_command(payload)
            packet = payload_to_packet(reply_magic_word, data if data else "")
            reply += packet
            payload = self.packet_pool.get_payload()
        return reply

    def run(self):
        sock_server_addr = admin_dir + "/profile-" + self.profile.name + ".sock"
        try:
            if os.path.exists(sock_server_addr):
                os.unlink(sock_server_addr)
        except:
            self.log_error("unable to bind socket")

        def process_data(data):
            return self.socket_server_process_data(data)
        def log_error(*args):
            return self.log_error(*args)
        def log_debug(*args):
            return self.log_debug(*args)
        self.socket_server = SocketServer(sock_server_addr, action=process_data, print_info=log_debug, print_error=log_error)

        try:
            self.socket_server.run()
            if os.path.exists(sock_server_addr):
                os.unlink(sock_server_addr)
        except Exception as e:
            self.log_error(traceback.format_exc())
            self.log_error("from socket server: ", e)
        self.log_debug("exiting command processor thread, bye!")

    def cancel(self):
        if self.socket_server:
            self.socket_server.cancel()
