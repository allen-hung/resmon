import os
import sys
import signal
import time
import fcntl
import traceback
import threading
import errno
from resource import ResourceMachine
from common import admin_dir
from log import LogDebug, LogInfo, LogError, LogFatal
from command import CommandProcessor

def print_error(msg):
    sys.stderr.write("\033[91m%s\033[0m\n" % msg)
    sys.exit(1)

def create_folder(dir):
    if os.path.exists(dir):
        if not os.path.isdir(dir):
            print_error("{} is not directory, exit on error!".format(dir))
    else:
        try:
            os.makedirs(dir)
        except OSError as e:
            msg = "Unable to create administration directory"
            if e.errno == errno.EACCES: msg += ", are you root?"
            print_error(msg)

class Lock(object):
    def __init__(self, filename):
        self.filename = filename
        try:
            self.handle = open(filename, "w")
        except IOError as e:
            msg = "Unable to obtain profile's exclusive lock"
            if e.errno == errno.EACCES: msg += ", are you root?"
            print_error(msg)

    def acquire(self, blocking=True):
        try:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(self.handle, flags)
        except IOError:
            return False
        return True

    def release(self):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        os.remove(self.filename)

class Daemon(object):
    def __init__(self, profile):
        self.profile = profile
        self.threads = []
        self.resources = []
        self.exit_sem = threading.Semaphore(0)
        def signal_handler(signal, frame):
            LogInfo("[{}:*] signal is caught, terminating process".format(self.profile.name))
            self.exit_sem.release()
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def start(self):
        create_folder(admin_dir)

        self.lock = Lock(admin_dir + "/profile-{}.lock".format(self.profile.name))
        if self.lock.acquire(False) is False:
            print_error("Process for profile '{}' is already running, exit!".format(self.profile.name))

        try:
            self.cp = CommandProcessor(self)
        except Exception as e:
            print_error(traceback.format_exc())

        ret = os.fork()
        if ret != 0: # in parent process...
            print "Process {} is created for profile '{}'".format(ret, self.profile.name)
            sys.exit(0)

        """ Go into background now """
        # TODO: redirect stdout, stderr
        try:
            self.run()
        except Exception as e:
            LogFatal(traceback.format_exc())

    def run(self):
        LogInfo("process {} spawned for profile '{}'".format(os.getpid(), self.profile.name))
        self.threads += [self.cp]
        for res_config in self.profile.resources:
            res = ResourceMachine(self.profile, res_config)
            self.threads += [res]
            self.resources += [res]

        for th in self.threads:
            th.start()

        """ waiting to exit main thread """
        while self.exit_sem.acquire(False) is False:
            time.sleep(999) # intends to be interrupted by signal

        """ main thread is waiting here for the completions of created threads """
        LogDebug("[{}:*] start to terminate everything".format(self.profile.name))
        for th in self.threads:
            th.cancel()
        for th in self.threads:
            th.join()
        LogInfo("[{}:*] main thread terminated".format(self.profile.name))

        self.lock.release()
        sys.exit(0)
