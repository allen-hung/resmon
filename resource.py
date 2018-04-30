import os
import sys
import threading
import subprocess
import signal
import psutil
import time
import tempfile
from log import LogDebug, LogInfo, LogError, LogFatal
from common import _enum_, admin_dir

MachineState = _enum_(
    "BEGIN",
    "START",
    "STOP",
    "STARTED",
    "STOPPED",
    "AUTOSTART",
    "MONITOR",
    "RECOVER",
    "FAILED",
    "IDLE",
    "EXIT",
)

ResourceState = _enum_(
    "STARTED",
    "STOPPED",
    "FAILED",
    "NONE"
)

class Command(object):
    def __init__(self, res):
        self.res = res
        self.script = res.config.Path
        self.pid = None
        self.timer = None
        self.cancel_lock = threading.Lock()
        self.res_lock = res.machine_lock
        self.tmpfile = tempfile.NamedTemporaryFile(mode="w+", prefix="msg-", suffix=".tmp", dir=admin_dir);
        self.abort = False

    @staticmethod
    def kill(pid):
        try:
            process = psutil.Process(pid)
            plist = [process] + process.children(recursive=True)
            for p in plist: p.kill()
        except:
            pass

    def cancel(self):
        with self.cancel_lock:
            self.abort = True
            timer = self.timer # intends to keep the reference to timer object
            if timer:
                timer.cancel()
            if self.pid:
                self.res.debug("kill pending command")
                Command.kill(self.pid)

    def run(self, command, timeout, env={}):
        def kill(pid):
            self.res.error("'{}' command timeout ({}s), forcibly kill it".format(command, timeout))
            Command.kill(pid)

        def terminate_thread():
            msg = "'{}' command is cancelled".format(command)
            self.res.debug(msg)
            self.res.debug("thread '{}' is terminated".format(threading.current_thread().name))
            raise SystemExit(msg)

        ret = -1
        with self.res_lock:
            self.res.debug("execute '{}' command".format(command))
            with self.cancel_lock:
                if self.abort:
                    terminate_thread()
                try:
                    argv = [self.script, command]
                    env["RESMOND_MESSAGE_FILE"] = self.tmpfile.name
                    start_time = time.time()
                    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, env=env)
                    self.pid = proc.pid
                    self.timer = threading.Timer(timeout, kill, [self.pid])
                    self.timer.start()
                except:
                    self.res.error("failed to issue '{}' command".format(command))
                    return 1
            """ leave cancel-lock """

            ret = proc.wait()
            self.timer.cancel()
            self.timer = None
            self.pid = None

        if self.abort:
            terminate_thread()
        elapsed_time = time.time() - start_time
        self.res.debug("'{}' command returns {}; spent {:.3f}s".format(command, ret, elapsed_time))
        msg = self.tmpfile.read()
        if msg:
            self.res.debug("returned message: {}".format(msg))
        return ret

class BaseState(object):
    def __init__(self, res):
        self.res = res
        self.config = res.config
        self.info = res.info
        self.debug = res.debug
        self.error = res.error

    def enter(self):
        NotImplementedError("enter is not implemented")

    def leave(self):
        pass

def SimpleMethodState(fn):
    class simple_class(BaseState):
        def __init__(self, res):
            super(simple_class, self).__init__(res)
        def enter(self):
            fn(self.res)
    return simple_class

@SimpleMethodState
def StartedState(self):
    self.res_state = ResourceState.STARTED
    if self.config.Monitor:
        self.state = MachineState.MONITOR
    else:
        self.state = MachineState.IDLE

@SimpleMethodState
def StoppedState(self):
    self.res_state = ResourceState.STOPPED
    self.state = MachineState.IDLE

@SimpleMethodState
def FailedState(self):
    self.res_state = ResourceState.FAILED
    self.state = MachineState.IDLE

@SimpleMethodState
def IdleState(self):
    pass

@SimpleMethodState
def ExitState(self):
    pass

class BeginState(BaseState):
    def __init__(self, res):
        super(BeginState, self).__init__(res)
        self.command = None

    def enter(self):
        def begin_task():
            ret = self.command.run("status", self.config.StatusTimeout)
            if ret == 0:
                self.debug("resource is already started")
                self.res.state = MachineState.STARTED
            else:
                self.debug("resource is not started")
                self.res.res_state = ResourceState.STOPPED
                if self.config.AutoStart:
                    self.res.state = MachineState.AUTOSTART
                else:
                    self.res.state = MachineState.STOPPED

        self.command = Command(self.res)
        timer = threading.Timer(0, begin_task, [])
        timer.start()

    def leave(self):
        if self.command:
            self.command.cancel()

class MonitorState(BaseState):
    def __init__(self, res):
        super(MonitorState, self).__init__(res)
        self.timer = None
        self.lock = threading.Lock()
        self.history = []
        self.history_max = self.config.MonitorThresholdTimes[1]
        self.history_min = self.config.MonitorThresholdTimes[0]
        self.left_counter = 0
        if self.config.Monitor is True:
            self.initial_counter = self.config.MonitorTimes
            if self.initial_counter == 9999:
                self.initial_counter = 2 ** 63
        else:
            self.initial_counter = 0
        self.command = None

    def enter(self):
        def do_monitor_command():
            tmp_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".tmp", dir=admin_dir);
            if tmp_file is None:
                self.error("cannot create intermediate file for 'monitor' command")
                return False, None

            env = { "RESMOND_MONITOR_VALUE_FILE": tmp_file.name }
            ret_code = self.command.run("monitor", self.config.MonitorTimeout, env)
            if ret_code != 0:
                return False, None

            tmp_file.seek(0)
            content = tmp_file.read()
            value = None if (content is None) else content.split("\n")[0]
            if value and value.isdigit():
                self.debug("received monitor value: {}".format(value))
                return True, int(value)
            else:
                self.error("'monitor' receives invalid value '{}'".format("null" if value is None else str(value)))
                return False, None

        def do_action_on_failure():
            """ Go to recover and pause monitor """
            if self.config.Action == "recover":
                self.error("recovering resource now")
                self.res.state = MachineState.RECOVER
            elif self.config.Action == "alert":
                self.error("alerting for resource failure")
                self.res.do_alert()
                # MONITOR => FAILED
                self.res.state = MachineState.FAILED
            else:
                self.error("do nothing on resource failure")
                # MONITOR => STARTED
                self.res.state = MachineState.STARTED # go on and just like nothing happened

        def monitor_task():
            self.timer = None
            start_time = time.time()
            self.debug("monitor resource")
            ret, value = do_monitor_command()
            if ret is False:
                value = self.config.MonitorDefault
                self.error("failed to run 'monitor' command, use '{}' by default".format(value))
            hit = (value >= self.config.MonitorThreshold)
            if hit:
                self.error("monitor return value ({}) exceeds threshold ({})".format(value, self.config.MonitorThreshold))
            """ Check if the history meets the least requirement to perform action """
            self.history += [hit]
            if len(self.history) > self.history_max:
                del self.history[0]
            if len(self.history) >= self.history_min:
                hits = [1 for h in self.history if h is True]
                if len(hits) >= self.history_min:
                    self.error("exceeded threshold {} times in the most recent {} monitors".format(len(hits), len(self.history)))
                    self.history = []
                    do_action_on_failure()
                    return
            """ Schedule next timer for monitor """
            with self.lock:
                self.left_counter -= 1
                if self.left_counter <= 0:
                    # MONITOR => IDLE
                    self.res.state = MachineState.IDLE
                    return
                elapsed_time = time.time() - start_time
                delay = self.config.MonitorInterval - elapsed_time
                if delay < 0: delay = 0
                self.timer = threading.Timer(delay, monitor_task, [])
                self.timer.start()

        self.left_counter = self.initial_counter
        if self.left_counter == 0:
            # MONITOR => IDLE
            self.res.state = MachineState.IDLE
            return
        self.info("resource is under monitoring")
        self.command = Command(self.res)
        delay = self.config.MonitorDelay
        self.timer = threading.Timer(delay, monitor_task, [])
        self.timer.start()

    def leave(self):
        with self.lock:
            self.left_counter = 0
            if self.command:
                self.command.cancel()
            if self.timer:
                self.timer.cancel()

class RecoverState(BaseState):
    def __init__(self, res):
        super(RecoverState, self).__init__(res)
        self.retry_max = res.config.RecoverRetryTimes
        self.lock = threading.Lock()
        self.timer = None
        self.command = None

    def enter(self):
        def recover_task():
            start_time = time.time()
            self.debug("recover resource")
            ret = self.command.run("recover", self.config.RecoverTimeout)
            if ret == 0:
                self.info("resource is recovered successfully")
                # RECOVER => STARTED
                self.res.state = MachineState.STARTED
                return

            self.retry += 1
            if self.retry >= self.retry_max:
                self.error("failed to recover resource for {} times, resource aborted!".format(self.retry_max))
                # RECOVER => FAILED
                self.res.state = MachineState.FAILED
                return

            """ Schedule next timer for monitor """
            with self.lock:
                if self.abort:
                    return
                elapsed_time = time.time() - start_time
                delay = self.config.RecoverRetryInterval - elapsed_time
                if delay < 0:
                    delay = 0
                self.error("failed to recover resource, retry in {:.3f}s later".format(delay))
                self.timer = threading.Timer(delay, recover_task, [])
                self.timer.start()

        self.res.res_state = ResourceState.FAILED
        self.info("resource is to be recovered")
        self.command = Command(self.res)
        self.abort = False
        self.retry = 0
        self.timer = threading.Timer(0, recover_task, [])
        self.timer.start()


    def leave(self):
        with self.lock:
            self.abort = True
            if self.command:
                self.command.cancel()
            if self.timer:
                self.timer.cancel()

class AutoStartState(BaseState):
    def __init__(self, res):
        super(AutoStartState, self).__init__(res)
        self.retry_max = res.config.StartRetryTimes
        self.timer = None
        self.lock = threading.Lock()
        self.command = None

    def enter(self):
        def start_task(retry):
            start_time = time.time()
            self.debug("start resource")
            ret = self.command.run("start", self.config.StartTimeout)
            if ret == 0:
                self.info("resource is started successfully")
                # AUTOSTART => STARTED
                self.res.state = MachineState.STARTED
                return

            if retry >= self.retry_max:
                self.error("failed to start resource for {} times, resource aborted!".format(self.retry_max))
                # AUTOSTART => FAILED
                self.res.state = MachineState.FAILED
                return

            """ Schedule timer for next start """
            with self.lock:
                if self.abort:
                    return
                elapsed_time = time.time() - start_time
                delay = self.config.StartRetryInterval - elapsed_time
                if delay < 0:
                    delay = 0
                self.error("failed to start resource, retry in {:.3f}s later".format(delay))
                self.timer = threading.Timer(delay, start_task, [retry + 1])
                self.timer.start()

        self.command = Command(self.res)
        self.abort = False
        self.info("resource is to be auto started")
        self.timer = threading.Timer(self.config.StartDelay, start_task, [1])
        self.timer.start()

    def leave(self):
        with self.lock:
            self.abort = True
            if self.command:
                self.command.cancel()
            if self.timer:
                self.timer.cancel()

class StartState(BaseState):
    def __init__(self, res):
        super(StartState, self).__init__(res)
        self.command = None

    def enter(self):
        def start_task():
            self.info("start resource")
            ret = self.command.run("start", self.config.StartTimeout)
            if ret == 0:
                self.info("resource is started successfully")
                # START => STARTED
                self.res.state = MachineState.STARTED
            else:
                self.error("failed to start resource")
                # START => FAILED
                self.res.state = MachineState.FAILED

        self.command = Command(self.res)
        timer = threading.Timer(0, start_task, [])
        timer.start()

    def leave(self):
        if self.command:
            self.command.cancel()

class StopState(BaseState):
    def __init__(self, res):
        super(StopState, self).__init__(res)
        self.command = None

    def enter(self):
        def stop_task():
            self.debug("stop resource")
            ret = self.command.run("stop", self.config.StartTimeout)
            if ret == 0:
                self.info("resource is stopped successfully")
            else:
                self.error("failed to stop resource")
            # STOP => STOPPED
            self.res.state = MachineState.STOPPED

        self.command = Command(self.res)
        timer = threading.Timer(0, stop_task, [])
        timer.start()

    def leave(self):
        if self.command:
            self.command.cancel()

class ResourceMachine(threading.Thread):
    def __init__(self, profile, res_config):
        name = profile.name + ":" + res_config.Name
        super(ResourceMachine, self).__init__(name=name)
        self.name = name
        self.config = res_config
        self.machine_lock = threading.Lock()
        self.sem = threading.Semaphore(0)
        self._res_state = ResourceState.NONE
        self._mac_state = None

    @property
    def state(self):
        return self._mac_state

    @state.setter
    def state(self, state):
        self._mac_state = state
        self.sem.release()

    @property
    def res_state(self):
        return self._res_state

    @res_state.setter
    def res_state(self, state):
        if state != self._res_state:
            self._res_state = state
            self.info("resource is {}".format(ResourceState.rev_map[state]))

    def info(self, *args):
        LogInfo("[{}] ".format(self.name), *args)

    def debug(self, *args):
        LogDebug("[{}] ".format(self.name), *args)

    def error(self, *args):
        LogError("[{}] ".format(self.name), *args)

    def cancel(self):
        self.state = MachineState.EXIT

    def do_alert(self):
        self.info("alert for resource failure, not implemented")

    def run(self):
        self.states = {
            MachineState.BEGIN:     BeginState(self),
            MachineState.START:     StartState(self),
            MachineState.STOP:      StopState(self),
            MachineState.STARTED:   StartedState(self),
            MachineState.STOPPED:   StoppedState(self),
            MachineState.AUTOSTART: AutoStartState(self),
            MachineState.FAILED:    FailedState(self),
            MachineState.MONITOR:   MonitorState(self),
            MachineState.RECOVER:   RecoverState(self),
            MachineState.IDLE:      IdleState(self),
            MachineState.EXIT:      ExitState(self)
        }

        def get_state_obj(state):
            if state in self.states:
                return self.states[state]
            self.debug("state class is undefined for {}".format(MachineState.rev_map[state]))
            return None

        self.debug("thread is created for resource")
        self.state = MachineState.BEGIN
        last_state = None

        while self.state != MachineState.EXIT:
            self.sem.acquire()
            """ leave the previous state """
            if last_state:
                self.debug("leave {} state".format(MachineState.rev_map[last_state]))
                obj = get_state_obj(last_state)
                if obj:
                    obj.leave()
            last_state = self.state

            """ enter the previous state """
            self.debug("enter {} state".format(MachineState.rev_map[self.state]))
            obj = get_state_obj(self.state)
            if obj:
                obj.enter()

        self.debug("exiting thread, bye!")
