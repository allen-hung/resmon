import os
import sys
import datetime

class DefaultLogFile(object):
    def printf(self, msg_level, msg, *args):
        strs = [msg] + [str(arg) for arg in args]
        print "".join(strs)

defaultLog = DefaultLogFile()

class LogFile(object):
    instance = defaultLog
    banners = ["<fatal> ", "<error> ", "", "<debug> "]

    def __new__(cls, *args):
        if LogFile.instance is defaultLog:
            LogFile.instance = object.__new__(cls, *args)
        return LogFile.instance

    def __init__(self, fp, log_level, name):
        self.fp = fp
        self.log_level = log_level
        self.name = name

    def printf(self, msg_level, msg, *args):
        if msg_level > self.log_level:
            return
        strs = [msg] + [str(arg) for arg in args]
        td = datetime.datetime.now().strftime("%b %d %H:%M:%S")
        pid = os.getpid()
        banner = LogFile.banners[msg_level]
        sring = "{} [{}]: {}{}".format(td, pid, banner, "".join(strs))
        if len(sring) > 0 and sring[-1] != "\n":
            sring += "\n"
        self.fp.write(sring)

def LogFatal(msg, *args):
    LogFile.instance.printf(0, msg, *args)

def LogError(msg, *args):
    LogFile.instance.printf(1, msg, *args)

def LogInfo(msg, *args):
    LogFile.instance.printf(2, msg, *args)

def LogDebug(msg, *args):
    LogFile.instance.printf(3, msg, *args)
