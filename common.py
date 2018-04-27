import os
import sys
import socket
import select
import tempfile
import shutil
import struct
import binascii

admin_dir = "/var/run/resmon"
command_magic_word = b"\x02\xb7"
reply_magic_word = b"\x46\x17"

def _enum_(*sequential, **named):
    enums = dict(zip(sequential, range(len(sequential))), **named)
    reverse = dict((value, key) for key, value in enums.iteritems())
    enums["map"] = enums
    enums["rev_map"] = reverse
    return type('Enum', (), enums)

def payload_to_packet(magic_word, payload):
    assert len(magic_word) == 2
    packet_size = 2 + 4 + len(payload) + 4
    len_bytes = struct.pack("I", packet_size-1)
    packet = magic_word + len_bytes + payload
    crc = struct.pack("I", binascii.crc32(packet) & 0xFFFFFFFF)
    packet += crc
    return packet

class PacketPool(object):
    # TODO: LogDebug should be replaced by delegate
    def __init__(self, magic_word):
        assert len(magic_word) == 2
        self.buffer = b""
        self.mw = magic_word
        self.payloads = []

    def feed_buffer(self, buffer):
        self.buffer += buffer
        self.parse_payload()

    def parse_payload(self):
        """ 2 bytes MW, 4 bytes LEN, N bytes Payload, 4 bytes CRC """
        while len(self.buffer) >= 10:
            """ check magic word """
            if self.buffer[0:2] != self.mw:
                #LogDebug("drop all buffer due to incorrect magic word")
                self.buffer = b"" # drop entire buffer

            """ extract the value from length field """
            length = struct.unpack("I", self.buffer[2:6])[0] + 1
            #print "packet len", length, "buffer len", len(self.buffer)
            if len(self.buffer) < length:
                #LogDebug("imcompleted packet will be processed later")
                break

            """ verify the packet CRC """
            calculated_crc = struct.pack("I", binascii.crc32(self.buffer[:length-4]) & 0xFFFFFFFF)
            if calculated_crc != self.buffer[length-4:length]:
                pass
            else:
                payload = self.buffer[6:length-4]
                self.payloads.append(payload)
            self.buffer = self.buffer[length:]

    def get_payload(self):
        if len(self.payloads) > 0:
            paylod = self.payloads[0]
            del self.payloads[0]
            return paylod
        return None

    def has_payload(self):
        return len(self.payloads) > 0

    def is_empty(self):
        return (self.buffer) == 0

class SocketServer(object):
    @staticmethod
    def default_print_info(*args):
        print " ".join([str(arg) for arg in args])

    @staticmethod
    def default_print_error(*args):
        print >>sys.stderr, " ".join([str(arg) for arg in args])

    def make_self_pipe(self):
        try:
            self.tmpdir = tempfile.mkdtemp()
            pipe_name = os.path.join(self.tmpdir, "fifo")
            os.mkfifo(pipe_name)
            self.self_pipe = open(pipe_name, "r+b", 0)
        except Exception as e:
            raise RuntimeError("failed to start socket server: " + str(e))

    def __init__(self, server_addr, action, print_info=None, print_error=None):
        self.server_addr = server_addr
        self.running = False
        self.action = action
        self.self_pipe = None
        self.log_info = SocketServer.default_print_info if (print_info is None) else print_info
        self.log_error = SocketServer.default_print_error if (print_error is None) else print_error

    def cancel(self):
        self.running = False
        if self.self_pipe:
            self.self_pipe.write("x")

    def run(self):
        try:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(self.server_addr)
            server.listen(5)
        except:
            raise RuntimeError("unable to bind socket to " + self.server_addr)

        self.log_info("socket server is bound to {}".format(self.server_addr))
        self.make_self_pipe()
        inputs = [server, self.self_pipe]
        outputs = []
        conns = {}

        def close_connection(x):
            if x in inputs: inputs.remove(x)
            if x in outputs: outputs.remove(x)
            x.close()
            if x in conns:
                self.log_info("close connection with the client at '{}'".format(conns[x].address))
                del conns[x]
            else:
                self.log_info("connection is closed")

        def Connection(conn, address):
            instance = dict(conn=conn, address=address, reply_queue=[])
            return type("Connection", (), instance)

        self.running = True
        while self.running:
            readable, writable, exceptional = select.select(inputs, outputs, inputs)
            """ dealing with connection closing """
            for x in exceptional:
                if x is server:
                    self.running = True
                    self.log_error("error in socket server, aborting service...")
                elif x in conns:
                    close_connection(x)
                elif x in inputs:
                    inputs.remove(x)
            """ dealing with data-receiving or connection accept """
            for x in readable:
                if x is server:
                    conn, client_addr = server.accept()
                    conn.setblocking(0)
                    inputs.append(conn)
                    conns[conn] = Connection(conn, client_addr)
                    self.log_info("connection is established with the client at '{}'".format(client_addr))
                elif x in conns:
                    self.log_info("select: ", readable, exceptional)
                    try:
                        data = x.recv(8192)
                    except socket.error as e:
                        self.log_error("error in receiving data: ", e)
                        close_connection(x)
                        continue
                    if data:
                        reply = self.action(data)
                        if reply:
                            conns[x].reply_queue.append(reply)
                            outputs.append(x)
                    else:
                        close_connection(x)
                elif x in inputs:
                    inputs.remove(x)
            """ dealing with sending data """
            for x in writable:
                if x in conns:
                    queue = conns[x].reply_queue
                    if len(queue) == 0:
                        """ nothing to write, stop watching """
                        if x in outputs:
                            outputs.remove(x)
                    else:
                        max_tx_len = 8192
                        if len(queue[0]) > max_tx_len:
                            reply = queue[0][:max_tx_len]
                            queue[0] = queue[0][max_tx_len:]
                        else:
                            reply = queue[0]
                            del queue[0]
                        try:
                            x.sendall(reply)
                        except socket.error as e:
                            self.log_error("error in sending data: ", e)
                            close_connection(x)
                elif x in outputs:
                    outputs.remove(x)
            """ end of while loop """

        try:
            server.close()
            shutil.rmtree(self.tmpdir)
        finally:
            self.log_info("socket service exited")
