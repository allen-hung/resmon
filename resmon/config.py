import os
import sys
import re
import log

config_dir_path = "/etc/resmon"
default_log = "/var/log/resmon.log"
default_log_level = 1
default_timeout = 30

id_regex = re.compile("^[_a-zA-Z]\\w{0,62}$")

def print_error(msg):
    sys.stderr.write("\033[91m%s\033[0m\n" % msg)

def print_warn(msg):
    sys.stderr.write("\033[93m%s\033[0m\n" % msg)

def config_error(fn, ln, msg):
    print_error("Line {}, {}: {}".format(ln, fn, msg))

def config_warn(fn, ln, msg):
    print_warn("Line {}, {}: {}".format(ln, fn, msg))

class IDict(dict):
    def __setitem__(self, key, value):
        super(IDict, self).__setitem__(key.lower(), value)
    def __getitem__(self, key):
        return super(IDict, self).__getitem__(key.lower())
    def __contains__(self, key):
        return super(IDict, self).__contains__(key.lower())

class GeneralConfig(object):
    def __init__(self, filename):
        self.config = IDict()
        self.filename = filename

    def __getattr__(self, key):
        return self.config[key]

    def add(self, line, key, value):
        def _assert(condition, msg):
            if not condition:
                config_error(self.filename, line, msg)
                sys.exit(1)

        def icmp(a, b):
            return a.lower() == b.lower()

        _assert(key not in self.config, "'{}' is already specified".format(key))
        if icmp(key, "Profile"):
            _assert(id_regex.match(value), "'{}' is not a valid profile name".format(value))
        elif icmp(key, "LogFile"):
            """ path validation left out to complete() """
            pass
        elif icmp(key, "LogLevel"):
            _assert(value.isdigit() and int(value)>=0 and int(value)<=3,
                "'{}' is not valid for '{}'".format(value, key))
            value = int(value)
        elif icmp(key, "DefaultTimeout"):
            _assert(value.isdigit() and int(value) > 0,
                "'{}' is not valid for '{}'".format(value, key))
            value = int(value)
        else:
            _assert(False, "'{}' is not a valid key".format(key))
        self.config[key] = value

    def complete(self):
        def _assert(condition, msg):
            if not condition:
                print_error("In [General] session, " + msg)
                sys.exit(1)
        def exists(key):
            return key in self.config

        if not exists("Profile"):
            name = self.filename.split(".")[0]
            _assert(id_regex.match(name), "'Profile' is not specified while the config filename could not be a legal profile name")
            self.config["Profile"] = name
            print_warn("'Profile' is not specified and defaults to '{}'".format(name))

        _assert(exists("Profile"), "'Profile' must be specified!")
        if not exists("LogFile"):
            self.config["LogFile"] = default_log
        if not exists("LogLevel"):
            self.config["LogLevel"] = default_log_level
        if not exists("DefaultTimeout"):
            self.config["DefaultTimeout"] = default_timeout

        _assert(not os.path.isdir(self.config["LogFile"]),
            "'{}' cannot be a directory!".format(self.config["LogFile"]))
        try:
            filename = self.config["LogFile"]
            fp = open(filename, "a", 0)
        except IOError:
            _assert(False, "cannot open '{}' to write!".format(self.config["LogFile"]))
        self.config["LogFile"] = log.LogFile(fp, self.config["LogLevel"], filename)

class ResConfig(object):
    int_keys = [
        "StartDelay", "StartRetryInterval", "MonitorDelay", "MonitorInterval", "MonitorTimes"]
    positive_int_keys = [
        "StartRetryTimes", "MonitorTimeout", "RecoverTimeout", "RecoverRetryTimes", "RecoverRetryInterval",
        "StartTimeout", "StopTimeout", "RestartTimeout", "StatusTimeout"]

    def __init__(self, filename, line):
        self.config = IDict()
        self.filename = filename
        self.start_line = line

    def __getattr__(self, key):
        return self.config[key]

    def add(self, line, key, value):
        def _assert(condition, msg):
            if not condition:
                config_error(self.filename, line, msg)
                sys.exit(1)

        def verify_int_value(key, value, lower=0, upper=2**64-1):
            _assert(value.isdigit() and int(value) >= lower and int(value) <= upper,
                "'{}' is not valid for '{}'".format(value, key))
            return int(value)

        def icmp(a, b):
            return a.lower() == b.lower()

        def imatch(key, list):
            for k in list:
                if icmp(k, key): return True
            return False

        _assert(key not in self.config, "'{}' is already specified".format(key))

        if imatch(key, ResConfig.int_keys):
            value = verify_int_value(key, value)
        elif imatch(key, ResConfig.positive_int_keys):
            value = verify_int_value(key, value, 1)
        elif icmp(key, "MonitorThreshold"):
            value = verify_int_value(key, value, 1, 100)
        elif icmp(key, "MonitorDefault"):
            value = verify_int_value(key, value, 0, 100)
        elif icmp(key, "Name"):
            _assert(id_regex.match(value), "'{}' is not a valid name".format(value))
        elif icmp(key, "AutoStart") or icmp(key, "Monitor"):
            if value.lower() == "yes":
                value = True
            elif value.lower() == "no":
                value = False
            else:
                _assert(False, "'{}' is not valid for '{}'".format(value, key))
        elif icmp(key, "Path"):
            """ path validation left out to complete() """
            pass
        elif icmp(key, "Action"):
            value = value.lower()
            _assert(value in ["none", "recover", "alert"],
                "'{}' is not valid for 'Action'".format(value))
        elif icmp(key, "MonitorThresholdTimes"):
            strings = value.split(",")
            _assert(len(strings) == 1 or len(strings) == 2,
                "'{}' is not valid for 'MonitorThresholdTimes'".format(value))
            for string in strings:
                _assert(string.isdigit() and int(string) > 0,
                    "'{}' is not valid for 'MonitorThresholdTimes'".format(string))
            if len(strings) == 1:
                strings += [strings[0]]
            numbers = (int(strings[0]), int(strings[1]))
            _assert(numbers[1] >= numbers[0],
                "'{}' is not valid for 'MonitorThresholdTimes'".format(value))
            value = numbers
        else:
            _assert(False, "'{}' is not a valid key".format(key))
        self.config[key] = value

    def complete(self, common):
        """ Apply default values """
        def exists(key):
            return key in self.config

        def _assert(condition, msg):
            if not condition:
                config_error(self.filename, self.start_line, "in this resource, " + msg)
                sys.exit(1)
        default_values = [
            ("Name",           None), # None implies the value must be specifed
            ("AutoStart",      False),
            ("StartDelay",     0),
            ("MonitorTimeout", common.DefaultTimeout),
            ("RecoverTimeout", common.DefaultTimeout),
            ("StartTimeout",   common.DefaultTimeout),
            ("StopTimeout",    common.DefaultTimeout),
            ("StatusTimeout",  common.DefaultTimeout),
            ("Monitor",        False),
            ("MonitorTimes",   9999),
            ("Action",         "alert"),
            ("MonitorThreshold",50),
            ("MonitorThresholdTimes", (1, 1)),
            ("StartRetryTimes", 1),
            ("RecoverRetryTimes", 1),
            ("MonitorDefault",    0)
        ]
        for key, value in default_values:
            if not exists(key):
                _assert(value is not None, "'{}' must be specified".format(key))
                self.config[key] = value
        if self.config["Monitor"] is True:
            _assert(exists("MonitorInterval"), "'MonitorInterval' must be specified")

        dependant_default_values = [
            ("Path", config_dir_path + "/resource/" + self.config["Name"]),
            ("RestartTimeout", self.config["StartTimeout"] + self.config["StopTimeout"]),
            ("StartRetryInterval", self.config["StartTimeout"]),
            ("RecoverRetryInterval", self.config["RecoverTimeout"])
        ]
        for key, value in dependant_default_values:
            if not exists(key):
                self.config[key] = value

        """ second-level dependant default values """
        if not exists("MonitorDelay"):
            self.config["MonitorDelay"] = self.config["MonitorInterval"]

        """ Validate values """
        # Fails or just warn?
        _assert(os.path.isfile(self.config["Path"]),
            "path '{}' is not existent".format(self.config["Path"]))
        _assert(os.access(self.config["Path"], os.X_OK),
            "file '{}' is not executable".format(self.config["Path"]))
        _assert(self.config["MonitorInterval"] >= self.config["MonitorTimeout"],
            "'MonitorInterval' must not less than 'MonitorTimeout'") 
        _assert(self.config["RecoverRetryInterval"] >= self.config["RecoverTimeout"],
            "'RecoverRetryInterval' must not less than 'RecoverTimeout'")

def load_config(filename):
    general = None
    resource = None
    resources = []

    try:
        f = open(filename, 'r')
    except IOError as e:
        print_error("Failed to open file: {}".format(e))
        sys.exit(1)

    space_re = re.compile("^\s*$")
    session_re = re.compile("^\[(.*)\]\s*$")
    leading_space_re = re.compile("^\s+\S+")
    equation_re = re.compile("^([_a-zA-Z]\w*)=(\S*)\s*$")
    errors = 0

    for i, line in enumerate(f.readlines()):
        if errors >= 10:
            break

        """ remove the trailing newline """
        if len(line) > 0 and line[-1] == '\n':
            line = line[:-1]

        """ skip empty, space or commented off line """
        if len(line) == 0 or line[0] == ";" or line[0] == "#" or space_re.match(line):
            continue

        """ Verify if the line has leading sapce """
        if leading_space_re.match(line):
            config_error(filename, i+1, "improper space in line")
            errors += 1
            continue

        """ Determine if line matches a session: [session_name] """
        m = session_re.match(line)
        if m:
            inner = m.group(1)
            if inner.lower() == "general":
                if resource:
                    resources += [resource]
                    resource = None
                if general:
                    config_error(filename, i+1, "[General] session is already defined")
                    sys.exit(1)
                general = GeneralConfig(filename)
            elif inner.lower() == "resource":
                if resource:
                    resources += [resource]
                    resource = None
                resource = ResConfig(filename, i+1)
            else:
                config_error(filename, i+1, "illegal session "+m.group(0))
                errors += 1
            continue

        """ Determine if line matches an equation in the form of key=value """
        m = equation_re.match(line)
        if m:
            if resource is None and general is None:
                config_error(filename, i+1, "expect [General] or [Resource]")
                errors += 1
                continue
            if resource:
                resource.add(i+1, m.group(1), m.group(2))
            elif general:
                general.add(i+1, m.group(1), m.group(2))
            continue

        if "=" in line:
            config_error(filename, i+1, "improper space in line")
        else:
            config_error(filename, i+1, "illegal format")
        errors += 1

    if resource:
        resources += [resource]
        resource = None

    if errors > 1000: # <<<<<<<<<<<<<<<<<<<<<
        print_error("Exit on error!")
        sys.exit(1)

    """ second-pass compilation for general config """
    if not general:
        print_error("[General] session is not defined!")
        sys.exit(1)
    general.complete()

    """ second-pass compilation for resource configs """
    res_names = []
    for r in resources:
        r.complete(general)
        if r.Name in res_names:
            print_error("Multiple resource '{}' defined".format(r.Name))
            sys.exit(1)
        res_names += [r.Name]

    return type("Profile", (), dict(name      = general.profile, 
                                    logfile   = general.LogFile,
                                    resources = resources))
