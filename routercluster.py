import socket
import threading
import sys
import datetime
import pickle
import netifaces as ni
import uuid
import subprocess
import argparse
from enum import Enum
import logging
import time
import os
import netifaces

log = logging.getLogger(__name__)

class SystemInfo:

    def __init__(self, interface = None):
        self.os_type = sys.platform
        self.interface = None
        if interface is not None:
            self.interface = interface
            return

        if self.os_type == "darwin":
            self.interface = "en0"
        elif self.os_type.startswith("linux"):
            self.interface = "eth0"
        else:
            log.info("Distribution type is %s" % self.os_type)

    def initializeClient(self):
        self.systemTime = None
        self.interfaceAddress = self.getInterfaceAddress()
        self.fingerprint = str(uuid.uuid4())
        self.healthCheck = b''
        self.collect()

    def getInterfaceAddress(self):
        try:
            interface = ni.ifaddresses(self.interface)
        except ValueError:
            log.error("Failed to get interface information for %s" % self.interface)
            return None

        if netifaces.AF_INET not in interface:
            return None

        return interface[ni.AF_INET][0]['addr']


    def collect(self):
        collectLogsTimer = threading.Timer(5.0, self.collect)
        collectLogsTimer.daemon = True
        collectLogsTimer.start()
        self.healthCheck = subprocess.check_output(['./health-check.sh'])

    def getConfiguration(self):
        return "\n".join("%s:%s" % item for item in vars(self).items())

    def waitForEth1(self):
        address = self.getInterfaceAddress()
        while address is None:
            log.info("Waiting for interface %s to come up" % self.interface)
            time.sleep(1)
            address = self.getInterfaceAddress()

        log.info("%s is up: %s" % (self.interface, address))


class RouterCluster:
    def __init__(self, routerConfiguration): #RouterConfiguration type
        self.config = routerConfiguration
        self.socket = None
        self.port = 8019
        self.routersDict = dict()
        self.routeCmd = None

    def printConfiguration(self):
        log.info("Configuration type is %s" % self.config.type)

    def expireInactive(self):
        inactiveConnectionsTimer = threading.Timer(2.0, self.expireInactive)
        inactiveConnectionsTimer.daemon = True
        inactiveConnectionsTimer.start()
        now = datetime.datetime.utcnow()
        ipRouteCmd = ""
        for key, value in self.routersDict.items():
            storedTime = value.systemTime
            if storedTime is None:
                continue
            bufferedTime = storedTime + datetime.timedelta(seconds=5)
            if bufferedTime < now:
                log.info("expring connection %s" % key)
                self.routersDict.pop(key, None)
            else:
                if value.healthCheck is not None:
                    for line in value.healthCheck.splitlines():
                        if "sshuttle" in line:
                            count = line.split(":")[1]
                            if int(count) > 0:
                                if ipRouteCmd != "":
                                    ipRouteCmd += " "
                                ipRouteCmd += "nexthop via %s dev eth1 weight 1" % value.interfaceAddress

        if (ipRouteCmd == ""):
            if self.routeCmd is not None:
                log.info("No viable routers")
                self.deleteDefaultRoute()
            self.routeCmd = None
        else:
            if self.routeCmd != ipRouteCmd:
                self.routeCmd = ipRouteCmd
                log.info("Changing routes to %s" % ipRouteCmd)
                self.setDefaultRoute()

    def executeCommand(self, command):

        log.info(command)
        if sys.platform == "darwin":
            return
        commandParts = command.split(" ")
        subprocess.call(commandParts)


    def flushRouteCache(self):
        command = "ip route flush cache"
        self.executeCommand(command)

    def deleteDefaultRoute(self):
        command = "ip route del table workspaces default"
        self.executeCommand(command)
        self.flushRouteCache()

    def setDefaultRoute(self):
        command = "ip route replace table workspaces default %s" % self.routeCmd
        self.executeCommand(command)
        self.flushRouteCache()

    def listen(self):
        log.info("Listening ...")

        self.expireInactive()

        # create an INET, STREAMing socket
        self.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.socket.bind((self.config.serverEndpoint, self.port))
        # become a server socket
        self.socket.listen(5)

        while True:
            conn, addr = self.socket.accept()
            found = 0
            # don't log localhost. Other 'new' connections are logged upon connection
            if addr[0] != "127.0.0.1":
                for _, value in self.routersDict.items():
                    if addr[0] == value.interfaceAddress:
                        found = 1
                        break

                if not found:
                    log.info("Received connection from new host %s" % addr[0])

            storedData = b''
            while 1:
                data = conn.recv(1024)
                if not data: break
                storedData += data

            try:
                reportData = pickle.loads(storedData)
                reportData.systemTime = datetime.datetime.utcnow()
                report = {reportData.fingerprint: reportData}
                self.routersDict.update(report)
            except Exception as e:
                log.error("Failed to load received data: %s" % e)

    def report(self):
        log.info("Reporting ...")
        self.systemInfo = SystemInfo()
        self.systemInfo.initializeClient()
        self.sendReport()

    def sendReport(self):
        sendReportTimer = threading.Timer(2.0, self.sendReport)
        sendReportTimer.daemon = True
        sendReportTimer.start()

        self.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        try:
            self.socket.connect((self.config.serverEndpoint, self.port))
            data = pickle.dumps(self.systemInfo)
            dataLength = len(data)
            dataPosition = 0
            while dataPosition != dataLength:
                sent = self.socket.send(data[dataPosition:])
                if sent == 0:
                    raise RuntimeError("socket connection broken")
                dataPosition += sent

            if self.socket is not None:
                self.socket.close()

        except socket.error, exc:
            log.info("Caught exception socket.error : %s" % exc)

        while True: time.sleep(100)

    def start(self):
        if self.config.type == ClusterType.SERVER:
            systemInfo = SystemInfo("eth1")
            systemInfo.waitForEth1()
            self.listen()
        elif self.config.type == ClusterType.CLIENT:
            self.report()


class ClusterType(Enum):
    SERVER = 1
    CLIENT = 2


class RouterConfiguration:

    def __init__(self, args):
        self.client = None
        self.server = None
        self.serverEndpoint = None
        self.type = None

        if args.server:
            self.type = ClusterType.SERVER
            self.serverEndpoint = "0.0.0.0"
        elif args.client:
            self.type = ClusterType.CLIENT
            if args.serverHost is None:
                raise Exception("--serverHost must be specified when --client is specified")
            self.serverEndpoint = args.serverHost
        else:
            raise Exception("Either --client or --server must be specified")

        self.initLogging(args.logLevel)


    def initLogging(self, logLevel):
        if logLevel is None:
            logLevel = "INFO"

        # log level
        numeric_log_level = getattr(logging, logLevel.upper(), 10)

        # log format
        log_format = "%(asctime)s [%(levelname)s] %(message)s"

        # apply log settings
        log.setLevel(numeric_log_level)
        type = "server" if self.type is ClusterType.SERVER else "client"
        filename = os.path.splitext(os.path.basename(__file__))[0]
        logDirPath = "/var/log/%s" % filename

        if not os.path.exists(logDirPath):
            try:
                os.makedirs(logDirPath)
            except OSError:
                raise

        logFileName = "%s/%s-%s.log" % (logDirPath, filename, type)
        logging.basicConfig(filename=logFileName, format=log_format, datefmt='%Y-%m-%d %H:%M:%S')
        consoleOutput = logging.StreamHandler(sys.stdout)
        consoleOutput.setFormatter(logging.Formatter(log_format))
        log.addHandler(consoleOutput)

    def initialize(self):
        log.info("Initializing")
        clusterInstance = RouterCluster(self)
        clusterInstance.printConfiguration()
        try:
            clusterInstance.start()
        except KeyboardInterrupt:
            log.info("User requested exit")
            pass

class ClusterArgParser():
    def __init__(self):
        self.args = None
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--logLevel", help="Log level")
        self.parser.add_argument("--client", help="Run the routercluster as a client", action="store_true")
        self.parser.add_argument("--server", help="Run the routercluster as a server", action="store_true")
        self.parser.add_argument("--serverHost", help="Where the client should report to")

    def parseArgs(self):
        self.args = self.parser.parse_args()
        return self.args

if __name__ == "__main__":
    args = ClusterArgParser().parseArgs()
    RouterConfiguration(args).initialize()