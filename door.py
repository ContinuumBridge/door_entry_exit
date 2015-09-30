#!/usr/bin/env python
# door.py
# Copyright (C) ContinuumBridge Limited, 2014-2015 - All Rights Reserved
# Written by Peter Claydon
#

# Default values:
config = {
    "entry-exit": True,
    "in_pir_to_door_time": 60,
    "door_close_to_in_pir_time": 60,
    "door_open_to_in_pir_time": 60,
    "max_door_open_time": 120,
    "data_send_delay": 1
}

import sys
import os.path
import time
from cbcommslib import CbApp, CbClient
from cbconfig import *
import requests
import json
from twisted.internet import reactor

CONFIG_FILE                       = CB_CONFIG_DIR + "door_entry_exit.config"
CID                               = "CID164"  # Client ID

class EntryExit():
    def __init__(self):
        self.inside_triggered = False
        self.inside_pir_on = False
        self.door_open = False
        self.action = "nothing"
        self.checkExit = {}
        self.inside_pir_on_time = 0
        self.inside_pir_off_time = 0
        self.inside_pir_on = False
        self.door_open = False
        self.door_open_time = 0
        self.door_close_time = 0
        self.state = "idle"
        self.pirID = None
        self.magID = None
        self.bridge_id = None
        self.idToName = {}
        self.s = []
        self.waiting = False
        reactor.callLater(10, self.fsm)

    def setIDs(self, bridge_id, idToName):
        self.idToName = idToName
        self.bridge_id = bridge_id

    def onChange(self, devID, timeStamp, value):
        if devID == self.magID:
            sensor = "magsw"
        elif devID == self.pirID:
            sensor = "pir"
        else:
            sensor = "unknown"
        self.cbLog("debug", "EntryExit, onChange. sensor: " + sensor)
        if sensor == "pir":
            if value == "on":
                self.inside_pir_on_time = timeStamp
                self.inside_pir_on = True
            else:
                self.inside_pir_off_time = timeStamp
                self.inside_pir_on = False
        if sensor == "magsw":
            if value == "on":
                self.door_open = True
                self.door_open_time = timeStamp
            else:
                self.door_open = False
                self.door_close_time = timeStamp

    def fsm(self):
        # This method is called every second
        prev_state = self.state
        action = "none"
        if self.state == "idle":
            if self.door_open:
                if self.door_open_time - self.inside_pir_on_time < config["in_pir_to_door_time"] or self.inside_pir_on:
                    self.state = "check_going_out"
                else:
                    self.state = "check_coming_in"
        elif self.state == "check_going_out":
            if not self.door_open:
                self.state = "check_went_out"
        elif self.state == "check_went_out":
            t = time.time()
            if t - self.door_close_time > config["door_close_to_in_pir_time"]:
                if self.inside_pir_on or t - self.inside_pir_off_time < config["door_close_to_in_pir_time"] - 4:
                    action = "answered_door"
                    self.state = "idle"
                else:
                    action = "went_out"
                    self.state = "idle"
        elif self.state == "check_coming_in":
            if self.inside_pir_on:
                action = "came_in"
                self.state = "wait_door_close"
            elif time.time() - self.door_open_time > config["door_open_to_in_pir_time"]:
                action = "open_and_close"
                self.state = "wait_door_close"
        elif self.state == "wait_door_close":
            if not self.door_open:
                self.state = "idle"
            elif time.time() - self.door_open_time > config["max_door_open_time"]:
                action = "door_open_too_long"
                self.state = "wait_long_door_open"
        elif self.state == "wait_long_door_open":
            if not self.door_open:
                self.state = "idle"
        elif self.state == "wait_door_close":
            if not self.door_open:
                self.state = "idle"
        else:
            self.cbLog("warning", "door algorithm imposssible state")
            self.state = "idle"
        if self.state != prev_state:
            self.cbLog("debug", "checkExits, new state: " + self.state)
        if action != "none":
            self.cbLog("debug", "checkExits, action: " + action) 
            values = {
                "name": self.bridge_id + "/entry_exit/" + self.idToName[self.magID] + "/" + action,
                "points": [[int(time.time()*1000), 1]]
            }
            self.storeValues(values)
        reactor.callLater(1, self.fsm)

    def sendValues(self):
        msg = {"m": "data",
               "d": self.s
               }
        #self.cbLog("debug", "sendValues. Sending: " + str(json.dumps(msg, indent=4)))
        self.client.send(msg)
        self.s = []
        self.waiting = False

    def storeValues(self, values):
        self.s.append(values)
        if not self.waiting:
            self.waiting = True
            reactor.callLater(config["data_send_delay"], self.sendValues)

class App(CbApp):
    def __init__(self, argv):
        self.appClass = "monitor"
        self.state = "stopped"
        self.status = "ok"
        self.devices = []
        self.idToName = {} 
        self.entryExit = EntryExit()
        #CbApp.__init__ MUST be called
        CbApp.__init__(self, argv)

    def setState(self, action):
        if action == "clear_error":
            self.state = "running"
        else:
            self.state = action
        msg = {"id": self.id,
               "status": "state",
               "state": self.state}
        self.sendManagerMessage(msg)

    def onConcMessage(self, message):
        #self.cbLog("debug", "onConcMessage, message: " + str(json.dumps(message, indent=4)))
        if "status" in message:
            if message["status"] == "ready":
                # Do this after we have established communications with the concentrator
                msg = {
                    "m": "req_config",
                    "d": self.id
                }
                self.client.send(msg)
        self.client.receive(message)

    def onClientMessage(self, message):
        self.cbLog("debug", "onClientMessage, message: " + str(json.dumps(message, indent=4)))
        global config
        if "config" in message:
            if "warning" in message["config"]:
                self.cbLog("warning", "onClientMessage: " + str(json.dumps(message["config"], indent=4)))
            else:
                try:
                    newConfig = message["config"]
                    copyConfig = config.copy()
                    copyConfig.update(newConfig)
                    if copyConfig != config or not os.path.isfile(CONFIG_FILE):
                        self.cbLog("debug", "onClientMessage. Updating config from client message")
                        config = copyConfig.copy()
                        with open(CONFIG_FILE, 'w') as f:
                            json.dump(config, f)
                        self.cbLog("info", "Config updated")
                        self.readLocalConfig()
                        # With a new config, send init message to all connected adaptors
                        for i in self.adtInstances:
                            init = {
                                "id": self.id,
                                "appClass": self.appClass,
                                "request": "init"
                            }
                            self.sendMessage(init, i)
                except Exception as ex:
                    self.cbLog("warning", "onClientMessage, could not write to file. Type: " + str(type(ex)) + ", exception: " +  str(ex.args))

    def onAdaptorData(self, message):
        #self.cbLog("debug", "onAdaptorData, message: " + str(json.dumps(message, indent=4)))
        if message["characteristic"] == "binary_sensor":
            self.entryExit.onChange(message["id"], message["timeStamp"], message["data"])

    def onAdaptorService(self, message):
        self.cbLog("debug", "onAdaptorService, message: " + str(json.dumps(message, indent=4)))
        if self.state == "starting":
            self.setState("running")
        serviceReq = []
        for p in message["service"]:
            if p["characteristic"] == "binary_sensor":
                if "type" in p:
                    if p["type"] == "pir":
                        self.entryExit.pirID = message["id"]
                        serviceReq.append({"characteristic": "binary_sensor", "interval": 0})
                    else:
                        self.entryExit.magID = message["id"]
                        serviceReq.append({"characteristic": "binary_sensor", "interval": 0})
                else:
                    self.entryExit.magID = message["id"]
                    serviceReq.append({"characteristic": "binary_sensor", "interval": 0})
        msg = {"id": self.id,
               "request": "service",
               "service": serviceReq}
        self.sendMessage(msg, message["id"])
        self.cbLog("debug", "onAdaptorService, response: " + str(json.dumps(msg, indent=4)))

    def readLocalConfig(self):
        global config
        try:
            with open(CONFIG_FILE, 'r') as f:
                newConfig = json.load(f)
                self.cbLog("debug", "Read local config")
                config.update(newConfig)
        except Exception as ex:
            self.cbLog("warning", "Local config does not exist or file is corrupt. Exception: " + str(type(ex)) + str(ex.args))
        self.cbLog("debug", "Config: " + str(json.dumps(config, indent=4)))

    def onConfigureMessage(self, managerConfig):
        self.readLocalConfig()
        idToName2 = {}
        for adaptor in managerConfig["adaptors"]:
            adtID = adaptor["id"]
            if adtID not in self.devices:
                # Because managerConfigure may be re-called if devices are added
                name = adaptor["name"]
                friendly_name = adaptor["friendly_name"]
                self.cbLog("debug", "managerConfigure app. Adaptor id: " +  adtID + " name: " + name + " friendly_name: " + friendly_name)
                idToName2[adtID] = friendly_name
                self.idToName[adtID] = friendly_name.replace(" ", "_")
                self.devices.append(adtID)
        self.client = CbClient(self.id, CID, 5)
        self.client.onClientMessage = self.onClientMessage
        self.client.sendMessage = self.sendMessage
        self.client.cbLog = self.cbLog
        self.client.loadSaved()
        self.entryExit.client = self.client
        self.entryExit.cbLog = self.cbLog
        self.entryExit.setIDs(self.bridge_id, self.idToName)
        self.setState("starting")

if __name__ == '__main__':
    App(sys.argv)
