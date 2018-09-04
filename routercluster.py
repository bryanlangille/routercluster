import socket
import threading
import sys
import datetime
import pickle
import netifaces as ni
import uuid
import subprocess


class SystemInfo:

    def __init__(self):
        self.os_type = sys.platform
        self.interface = None
        if (self.os_type == "darwin"):
            self.interface = "en0"
        elif (self.os_type.startswith("linux")):
            self.interface = "eth0"
        else:
            print("interface is %s" % self.os_type)
        self.systemTime = None
        self.interfaceAddress = self.getInterfaceAddress()
        self.fingerprint = str(uuid.uuid4())
        self.healthCheck = b''
        self.collect()
        self.inService = 0

    def getInterfaceAddress(self):
        ni.ifaddresses(self.interface)
        return ni.ifaddresses(self.interface)[ni.AF_INET][0]['addr']

    def collect(self):
        threading.Timer(5.0, self.collect).start()
        self.healthCheck = subprocess.check_output(['./health_check.sh'])

    def getConfiguration(self):
        return "\n".join("%s:%s" % item for item in vars(self).items())


class RouterCluster:
    def __init__(self, config):
        self.config = config
        self.socket = None
        self.port = 8019
        self.routersDict = dict()
        self.routeCmd = None

    def printConfiguration(self):
        print("Configuration type is %s" % self.config.type)

    def expireInactive(self):
        threading.Timer(2.0, self.expireInactive).start()
        now = datetime.datetime.utcnow()
        ipRouteCmd = ""
        for key, value in self.routersDict.items():
            storedTime = value.systemTime
            if storedTime is None:
                continue
            bufferedTime = storedTime + datetime.timedelta(seconds=5)
            if bufferedTime < now:
                print("expring connection %s" % key)
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
                self.deleteDefaultRoute()
            self.routeCmd = None
            print("No viable routers")
        else:
            if self.routeCmd != ipRouteCmd:
                self.routeCmd = ipRouteCmd
                print("Changing routes to %s" % ipRouteCmd)
                self.setDefaultRoute()

    def flushRouteCache(self):
        command = "ip route flush cache"
        commandParts = command.split(" ")
        subprocess.call(commandParts)

    def deleteDefaultRoute(self):
        command = "ip route del table workspaces default"
        commandParts = command.split(" ")
        subprocess.call(commandParts)
        self.flushRouteCache()

    def setDefaultRoute(self):
        command = "ip route replace table workspaces default %s" % self.routeCmd
        print(command)
        commandParts = command.split(" ")
        subprocess.call(commandParts)
        self.flushRouteCache()

    def listen(self):
        print("Listening ...")

        self.expireInactive()

        # create an INET, STREAMing socket
        self.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.socket.bind((self.config.serverEndpoint, self.port))
        # become a server socket
        self.socket.listen(1)
        while True:
            conn, addr = self.socket.accept()
            #print("received connection from %s" % addr[0])
            storedData = b''
            while 1:
                data = conn.recv(2048)
                if not data: break
                storedData += data

            reportData = pickle.loads(storedData)
            reportData.systemTime = datetime.datetime.utcnow()
            report = {reportData.fingerprint: reportData}
            self.routersDict.update(report)

    def report(self):
        print("Reporting ...")
        self.systemInfo = SystemInfo()
        self.sendReport()

    def sendReport(self):
        threading.Timer(2.0, self.sendReport).start()
        self.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        try:
            self.socket.connect((self.config.serverEndpoint, self.port))
            sent = self.socket.send(pickle.dumps(self.systemInfo))
            if sent == 0:
                raise RuntimeError("socket connection broken")
            if self.socket is not None:
                self.socket.close()
        except socket.error, exc:
            print("Caught exception socket.error : %s" % exc)

    def start(self):
        if self.config.type == "server":
            self.listen()
        elif self.config.type == "client":
            self.report()


class Config:
    def __init__(self):
        self.type = sys.argv[1]
        self.serverEndpoint = None
        if self.type == "client":
            self.serverEndpoint = sys.argv[2]
        else:
            self.serverEndpoint = "0.0.0.0"


class RouterConfiguration:

    def readConfiguration(self):
        # read configuration
        print("Reading configuration")
        config = Config()
        return config

    def initialize(self):
        print("Initializing")
        config = self.readConfiguration()
        clusterInstance = RouterCluster(config)
        clusterInstance.printConfiguration()
        clusterInstance.start()

if __name__ == "__main__":
    RouterConfiguration().initialize()