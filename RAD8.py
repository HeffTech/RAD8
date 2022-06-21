#!/usr/bin/env python
import argparse
import logging
import time
import serial
import socket
import asyncio
import websockets
import datetime
import json

#setup logging
parser = argparse.ArgumentParser()
parser.add_argument("-log", "--loglevel", default="INFO")
args = parser.parse_args()

log_level = getattr(logging, args.loglevel.upper(), "INFO")

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] Line:%(lineno)d Msg:%(message)s",
    handlers=[
        logging.FileHandler("RAD8.log"),
        logging.StreamHandler()
    ]
)

logging.info("Logging level: " + logging.getLevelName(logging.getLogger().getEffectiveLevel()))

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        #doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception as err:
        logging.error(err)
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP

async def get_pulse_ox_data():
    #serial device definition  
    serial_device = "/dev/ttyUSB0"
    serial_conn = ""

    active_alarms = []
    alarm_history = []
    alarm_history_file = "alarm_history.json"

    try:
        with open(alarm_history_file, "r") as f:
            alarm_history = json.load(f)
    except Exception as err:
        logging.error(err)

    while True:
        rawdata = ""

        data = {}
        data["interface_timestamp"] = str(datetime.datetime.now().strftime("%m/%d/%y %H:%M:%S"))

        if serial_conn:
            logging.debug("serial_conn defined")
            data["serial_conn"] = True
            try:
                #flush input so we only have the most current event, otherwise a queue builds up
                serial_conn.flushInput()

                #read line, convert to string, remove the prefix: b'
                rawdata = str(serial_conn.readline()).lstrip("b'")
                #rawdata = "05/20/21 14:06:02 SN=0000182948 SPO2=098% BPM=101 PI=--.--% SPCO=--.-% SPMET=--.-% DESAT=-- PIDELTA=+-- ALARM=0010 EXC=000824\r\n"
                logging.debug("raw data: " + rawdata)
            except Exception as err:
                logging.error(err)
                serial_conn = ""

        else:
            logging.debug("serial_conn not defined")
            data["serial_conn"] = False
            try:
                logging.info("opening connection to device: '" + serial_device + "'")
                serial_conn = serial.Serial(serial_device, 9600, timeout=1)
                logging.info("successfully opened connection to device: '" + serial_device + "'")
            except Exception as err:
                logging.error(err)

        if rawdata:
            logging.debug("data received from serial interface")
            data["serial_data"] = True

            try:
                #truncated last 5 characters: \r\n'
                rawdata = rawdata[:-5]
                logging.debug("truncated data: " + rawdata)

                #split string by whitespace character
                elements = rawdata.split()
            
		    	#timestamp from the Masimo RAD-8 is based off the first 2 elements
                data["rad8_timestamp"] = elements[0] + " " + elements[1]

                #all other elements are key-value pairs, clean up the place placeholder and unit characters 
                for item in elements:
                    if "=" in item:
                        kv_pair = item.split("=")
                        data[kv_pair[0]] = kv_pair[1].replace(".-","").replace("-","").replace("%","").replace("+","")

            except Exception as err:
                logging.error("failed to parse rawdata: '" + rawdata + "'")
                logging.error(err)

            #build EXC bitmask
            try:
                EXC = bin(int(data["EXC"][-3:],16))[2:].zfill(12)
            except Exception as err:
                logging.error("failed to convert EXC to bitmask: '" + data["EXC"] + "'")
                logging.error(err)

            #decode EXC bitmask
            active_exc_list = []	
            if EXC == "000000000000":
                active_exc_list.append("Normal operation, no exceptions")
            else:
                if int(EXC[11]): active_exc_list.append("No Sensor")
                if int(EXC[10]): active_exc_list.append("Defective Sensor")
                if int(EXC[9]): active_exc_list.append("Low Perfusion")
                if int(EXC[8]): active_exc_list.append("Pulse Search")
                if int(EXC[7]): active_exc_list.append("Interference")
                if int(EXC[6]): active_exc_list.append("Sensor Light")
                if int(EXC[5]): active_exc_list.append("Ambient Light")
                if int(EXC[4]): active_exc_list.append("Unrecognized Sensor")
                if int(EXC[3]): active_exc_list.append("reserved")
                if int(EXC[2]): active_exc_list.append("reserved")
                if int(EXC[1]): active_exc_list.append("Low Signal IQ")
                if int(EXC[0]): active_exc_list.append("Masimo SET")			
            data["active_exc_list"] = active_exc_list

            alarms_response = process_alarms(data["ALARM"], data["interface_timestamp"], data["rad8_timestamp"], active_alarms, alarm_history, alarm_history_file)

            active_alarms = alarms_response["active_alarms"]
            alarm_history = alarms_response["alarm_history"]

            data["active_alarms"] = active_alarms
            data["alarm_history"] = alarm_history
        else:
            logging.debug("no data received from serial interface")
            data["serial_data"] = False
            data["alarm_history"] = alarm_history

        #convert to json and save to global variable that can be accessed by send_pulse_ox_data()
        global rad8data
        rad8data = json.dumps(data, indent=4)
        logging.debug("updating rad8data: " + rad8data)

        #sleep for 1 second
        await asyncio.sleep(1)

def process_alarms(ALARM, interface_timestamp, rad8_timestamp, active_alarms, alarm_history, alarm_history_file):
    alarm_history_max = 40
    #Unlike the EXC value, there is no documentation for decoding the ALARM value. Masimo technical service is unwilling share how the ALARM value is encoded. Apparently, this is “proprietary information”.
    #So, using the same decoding logic as we did with EXC, we're able to identify low O2, low heart rate, high heart rate, and sensor off. sensor off appears to be made up of a combination of 2 bits, so we convert it to bit 6, which appears unused. This simplifies the logic used for tracking active alarms, IMHO.

    try:
        #build ALARM bitmask
        ALARM = bin(int(ALARM[-2:],16))[2:].zfill(6)
        if (int(ALARM[2]) and int(ALARM[4])):
            #convert the 'sensor off' alarm to the 6th bit
            ALARM = "1".zfill(6)
    except Exception as err:
        logging.error("failed to convert ALARM to bitmask: '" + ALARM + "'")
        logging.error(err)

    for bit in range(2, 5):
        try:
            alarm_index = find_index(active_alarms, "bit", bit)
        except ValueError:
            alarm_index = None

        if (alarm_index == None):
            #alarm bit not found in active_alarms
            if (int(ALARM[bit])):
                #create new alarm
                alarm_item = {}
                alarm_item["bit"] = bit
                alarm_item["alarm_text"] = get_alarm_text(bit)
                alarm_item["silenced"] = ALARM[0]
                alarm_item["start_interface_timestamp"] = interface_timestamp
                alarm_item["start_rad8_timestamp"] = rad8_timestamp
                alarm_item["end_interface_timestamp"] = None
                alarm_item["end_rad8_timestamp"] = None
                active_alarms.append(alarm_item)

        else:
            #alarm bit found in active_alarms, therefore was previously active
            active_alarm = active_alarms[alarm_index]

            #delete the item from the active_alarms list, depending on the current status it will either be moved to alarm_history or added back with current values 
            del active_alarms[alarm_index]

            #retain the all values of the event, except bit 0 (silenced)
            alarm_item = {}
            alarm_item["bit"] = active_alarm["bit"]
            alarm_item["alarm_text"] = active_alarm["alarm_text"]
            alarm_item["silenced"] = ALARM[0]
            alarm_item["start_interface_timestamp"] = active_alarm["start_interface_timestamp"]
            alarm_item["start_rad8_timestamp"] = active_alarm["start_rad8_timestamp"]

            if (int(ALARM[bit])):
                #alarm was previously active, retain end timestamp
                alarm_item["end_interface_timestamp"] = active_alarm["end_interface_timestamp"]
                alarm_item["end_rad8_timestamp"] = active_alarm["end_rad8_timestamp"]
                active_alarms.append(alarm_item)
            else:
                #alarm is no longer active, set the end_timestamp
                alarm_item["end_interface_timestamp"] = interface_timestamp
                alarm_item["end_rad8_timestamp"] = rad8_timestamp

                #add alarm_item to the alarm_history list
                try:
                    alarm_history.insert(0, alarm_item)
                except Exception as err:
                    logging.error("failed to update alarm_history")
                    logging.error(err)

                #truncate alarm_history
                try:      
                    while len(alarm_history) > alarm_history_max:
                        logging.info("deleting oldest record from alarm_history:" + json.dumps(alarm_history[len(alarm_history)-1], indent=4))
                        del alarm_history[len(alarm_history)-1]
                except Exception as err:
                    logging.error("failed to truncate alarm_history")
                    logging.error(err)

                #export alarm_history to file
                try:
                    with open(alarm_history_file, "w") as f:
                        json.dump(alarm_history, f)
                except Exception as err:
                    logging.error("failed to export alarm_history")
                    logging.error(err)

        #build response
        response = {}
        response["active_alarms"] = active_alarms
        response["alarm_history"] = alarm_history
    return response

def find_index(dicts, key, value):
    class Null: pass
    for i, d in enumerate(dicts):
        if d.get(key, Null) == value:
            return i
    else:
        raise ValueError("no dict with the key and value combination found")

def get_alarm_text(bit):
    if (bit == 2):
        text = "Low Heart Rate"
    elif (bit == 3):
        text = "High Heart Rate"
    elif (bit == 4):
        text = "Low O2"
    elif (bit == 5):
        text = "Sensor Off"
    else:
        text = "undefined"

    return text

async def send_pulse_ox_data(websocket, path):
    try:
        websocket_connection_id = "[client:"
        websocket_connection_id += websocket.remote_address[0]
        websocket_connection_id += "/"
        websocket_connection_id += str(websocket).split(" ")[3].replace(">","")
    except Exception as err:
        logging.error(err)
    finally:
        websocket_connection_id += "]"
        logging.info(websocket_connection_id + " opened connection")

    while True:
        #determines how often data is sent to the client based on whether or not the RAD8 is pushing data to the serial interface, value in seconds
        try:
            rad8data_json = json.loads(rad8data)
            if (rad8data_json["serial_data"]):
                sleep_schedule = 4
            else:
                sleep_schedule = 15
        except Exception as err:
            logging.error(err)

        #send data to client over websocket
        try:
            logging.debug(websocket_connection_id + " send_pulse_ox_data: " + rad8data)
            await websocket.send(rad8data)
        except Exception as err:
            logging.error(websocket_connection_id + " " + str(err))
            #break for any exception, these are usually because a client disconnected
            break
        finally:
            await asyncio.sleep(sleep_schedule)

    logging.info(websocket_connection_id + " closed connection")

rad8data = ""
tcp_port = 5678

while True:
    ip = get_ip()
    if (ip == "127.0.0.1") or ("169.254" in ip):
        logging.info("unable to obtain local IP address, will try again in 5 seconds")
        time.sleep(5)
    else:
        logging.info("local IP:" + ip)
        break;

try:
    #start websocket server, run forever
    logging.info("starting websocket server on: " + ip + ":" + str(tcp_port))
    server = websockets.serve(send_pulse_ox_data, ip, tcp_port)
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(get_pulse_ox_data())
    asyncio.ensure_future(server)
    loop.run_forever()
except KeyboardInterrupt:
    logging.info("KeyboardInterrupt executed by user")
finally:
    logging.info("closing the loop")
    loop.close()