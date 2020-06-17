#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from RPi_AS3935 import RPi_AS3935
import RPi.GPIO as GPIO
import thread
import time
from datetime import datetime

import socket
import os
import uuid

import ssl
import sys
import re
import json
import os.path
import argparse
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from colorama import init as colorama_init
from colorama import Fore, Back, Style
from configparser import ConfigParser
from unidecode import unidecode
import paho.mqtt.client as mqtt
import sdnotify
from signal import signal, SIGPIPE, SIG_DFL
signal(SIGPIPE,SIG_DFL)

script_version = "0.1"
project_name = 'lightning-detector-MQTT2HA-Daemon'
project_url = 'https://github.com/ironsheep/lightning-detector-MQTT2HA-Daemon'

if False:
    # will be caught by python 2.7 to be illegal syntax
    print('Sorry, this script requires a python3 runtime environment.', file=sys.stderr)


# Argparse
parser = argparse.ArgumentParser(description=project_name, epilog='For further details see: ' + project_url)
parser.add_argument('--config_dir', help='set directory where config.ini is located', default=sys.path[0])
parse_args = parser.parse_args()


# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()


# Logging function
def print_line(text, error = False, warning=False, sd_notify=False, console=True):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        else:
            print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
    timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
    if sd_notify:
        sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))

# Identifier cleanup
def clean_identifier(name):
    clean = name.strip()
    for this, that in [[' ', '-'], ['ä', 'ae'], ['Ä', 'Ae'], ['ö', 'oe'], ['Ö', 'Oe'], ['ü', 'ue'], ['Ü', 'Ue'], ['ß', 'ss']]:
        clean = clean.replace(this, that)
    clean = unidecode(clean)
    return clean

# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print_line('MQTT connection established', console=True, sd_notify=True)
        print()
    else:
        print_line('Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True)
        #kill main thread
        os._exit(1)

def on_publish(client, userdata, mid):
    #print_line('Data successfully published.')
    pass

# Load configuration file
config_dir = parse_args.config_dir

config = ConfigParser(delimiters=('=', ), inline_comment_prefixes=('#'))
config.optionxform = str
try:
    with open(os.path.join(config_dir, 'config.ini')) as config_file:
        config.read_file(config_file)
except IOError:
    print_line('No configuration file "config.ini"', error=True, sd_notify=True)
    sys.exit(1)

daemon_enabled = config['Daemon'].getboolean('enabled', True)

default_base_topic = 'home/nodes'
default_sensor_name = 'lightningdetector'

base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()
sensor_name = config['MQTT'].get('sensor_name', default_sensor_name).lower()
sleep_period = config['Daemon'].getint('period', 10)


# GPIO pin used for interrupts
default_intr_pin = 17
intr_pin = config['Sensor'].get('intr_pin', default_intr_pin)

default_i2c_bus = 1
default_i2c_address = 0x03

i2c_bus = config['Sensor'].get('i2c_bus', default_i2c_bus)
i2c_address = config['Sensor'].get('i2c_address', default_i2c_address)

default_detector_afr_gain_indoor = True
detector_afr_gain_indoor = config['Sensor'].get('detector_afr_gain_indoor', default_detector_afr_gain_indoor)

# noise_floor (0-7)
default_detector_noise_floor = 1
detector_noise_floor = config['Sensor'].get('detector_noise_floor', default_detector_noise_floor)

# number of strikes (def: 5, value 1,5,9,16), then are fired normally.
default_detector_min_strikes = 5
detector_min_strikes = config['Sensor'].get('detector_min_strikes', default_detector_min_strikes)

#  FIXME: UNONE - let's add value VALIDATION!!!

# Check configuration
#
#
#    TBA
#if not config['Sensors']:
#    print_line('No sensors found in configuration file "config.ini"', error=True, sd_notify=True)
#    sys.exit(1)


print_line('Configuration accepted', console=False, sd_notify=True)

# MQTT connection
print_line('Connecting to MQTT broker ...')
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

mqtt_client.will_set('{}/connected'.format(base_topic), payload='0', retain=True)

if config['MQTT'].getboolean('tls', False):
    # According to the docs, setting PROTOCOL_SSLv23 "Selects the highest protocol version
    # that both the client and server support. Despite the name, this option can select
    # “TLS” protocols as well as “SSL”" - so this seems like a resonable default
    mqtt_client.tls_set(
        ca_certs=config['MQTT'].get('tls_ca_cert', None),
        keyfile=config['MQTT'].get('tls_keyfile', None),
        certfile=config['MQTT'].get('tls_certfile', None),
        tls_version=ssl.PROTOCOL_SSLv23
    )

mqtt_username = os.environ.get("MQTT_USERNAME", config['MQTT'].get('username'))
mqtt_password = os.environ.get("MQTT_PASSWORD", config['MQTT'].get('password', None))

if mqtt_username:
    mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
    mqtt_client.connect(os.environ.get('MQTT_HOSTNAME', config['MQTT'].get('hostname', 'localhost')),
                        port=int(os.environ.get('MQTT_PORT', config['MQTT'].get('port', '1883'))),
                        keepalive=config['MQTT'].getint('keepalive', 60))
except:
    print_line('MQTT connection error. Please check your settings in the configuration file "config.ini"', error=True, sd_notify=True)
    sys.exit(1)
else:
    mqtt_client.publish('{}/connected'.format(base_topic), payload='1', retain=True)
    mqtt_client.loop_start()
    sleep(1.0) # some slack to establish the connection

sd_notifier.notify('READY=1')

# our lighting device
LD_TIMESTAMP = "timestamp"
LD_ENERGY = "energy"    # 21b value unsigned
LD_DISTANCE = "distance"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km
LD_COUNT = "count"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km

LDS_MIN_STRIKES = "min_strikes" # 1,5,9,16
LDS_LOCATION = "location" # indoors, outdoors
LDS_LCO_ON_INT = "disp_lco" # T/F where T means LCO is transmitting on Intr pin (can't detect when this is true)
LDS_NOISE_FLOOR = "noise_floor" # [0-7]


# Discovery Announcement

# what device are we on?

gw = os.popen("ip -4 route show default").read().split()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect((gw[2], 0))
ipaddr = s.getsockname()[0]
interface = gw[4]
ether = os.popen("ifconfig " + interface + "| grep ether").read().split()
mac = ether[1]
fqdn = socket.getfqdn()

uniqID = "AS3935-{}".format(mac.lower().replace(":", ""))

# Publish our MQTT auto discovery
detectorValues = OrderedDict([
    (LD_TIMESTAMP, dict(title="TimeStamp", device_class="timestamp", device_ident="yes")),
    (LD_ENERGY, dict(title="Energy")),
    (LD_DISTANCE, dict(title="Distance")),
    (LD_COUNT, dict(title="Count")),
])

print_line('Announcing Lightning Detection device to MQTT broker for auto-discovery ...')
#for [sensor_name, sensor_dict] in detectorValues.items():
state_topic = '{}/sensor/{}/state'.format(base_topic, sensor_name.lower())
settings_topic = '{}/sensor/{}/settings'.format(base_topic, sensor_name.lower())

for [sensor, params] in detectorValues.items():
    discovery_topic = 'homeassistant/sensor/{}/{}/config'.format(sensor_name.lower(), sensor)
    payload = OrderedDict()
    payload['name'] = "{} {}".format(sensor_name, sensor.title())
    payload['unique_id'] = "{}_{}".format(uniqID, sensor.title().lower())
    if 'device_class' in params:
        payload['device_class'] = params['device_class']
    payload['state_topic'] = state_topic
    payload['value_template'] = "{{{{ value_json.{} }}}}".format(sensor)
    if 'device_ident' in sensor_dict:
        payload['device'] = {
                'identifiers' : ["{}".format(uniqID)],
                'connections' : [["mac", mac.lower()], [interface, ipaddr]],
                'manufacturer' : '(Austria Micro Systems) ams AG',
                'name' : sensor_name,
                'model' : 'Lightning Detector (AS3935)',
                'sw_version': "v{}".format(script_version)
        }
    else:
         payload['device'] = {
                'identifiers' : ["{}".format(uniqID)],
         }
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)



# Initialize GPIO
GPIO.setmode(GPIO.BCM)

# pin used for interrupts
pin = intr_pin
# Rev. 1 Raspberry Pis should leave bus set at 0, while rev. 2 Pis should set
# bus equal to 1. The address should be changed to match the address of the
# sensor.
sensor = RPi_AS3935(address=i2c_address, bus=i2c_bus)
# Indoors = more sensitive (can miss very strong lightnings)
# Outdoors = less sensitive (can miss far away lightnings)
sensor.set_indoors(detector_afr_gain_indoor)
sensor.set_noise_floor(default_detector_noise_floor)
# Change this value to the tuning value for your sensor
sensor.calibrate(tun_cap=0x01)
# Prevent single isolated strikes from being logged => interrupts begin after 5 strikes, then are fired normally
sensor.set_min_strikes(detector_min_strikes)

last_alert = datetime.min
strikes_since_last_alert = 0


# We use a function to send tweet so that we can run it in a different thread and avoid spending too much time in the
# interrupt handle
#def send_tweet(tweet):
#   #api.update_status(tweet)

def send_settings(minStrikes, isIndoors, isDispLco, noiseFloor)
    settingsData = OrderedDict()
    settingsData[LDS_MIN_STRIKES] = minStrikes
    settingsData[LDS_LOCATION] = isIndoors
    settingsData[LDS_LCO_ON_INT] = isDispLco
    settingsData[LDS_NOISE_FLOOR] = noiseFloor

    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(settings_topic, json.dumps(data)))
    mqtt_client.publish('{}'.format(settings_topic), json.dumps(settingsData))
    sleep(0.5) # some slack for the publish roundtrip and callback function

def send_status(timestamp, energy, distance, strikeCount)
    data = OrderedDict()
    data[LD_TIMESTAMP] = timestamp
    data[LD_ENERGY] = energy
    data[LD_DISTANCE] = distance

    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(state_topic, json.dumps(data)))
    mqtt_client.publish('{}'.format(state_topic), json.dumps(data))
    sleep(0.5) # some slack for the publish roundtrip and callback function

# Interrupt handler
def handle_interrupt(channel):
    global last_alert
    global strikes_since_last_alert
    global sensor
    current_timestamp = datetime.now()
    time.sleep(0.003)
    reason = sensor.get_interrupt()
    if reason == 0x01:
        print("Noise level too high - adjusting")
        sensor.raise_noise_floor()
    elif reason == 0x04:
        print("Disturber detected. Masking subsequent disturbers")
        sensor.set_mask_disturber(True)
    elif reason == 0x08:
        print("We sensed lightning! (%s)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'))
        if (current_timestamp - last_alert).seconds < 3:
            print("Last strike is too recent, incrementing counter since last alert.")
            strikes_since_last_alert += 1
            return
        distance = sensor.get_distance()
        energy = sensor.get_energy()
        print("Energy: " + str(energy) + " - distance: " + str(distance) + "km")
        # Yes, it tweets in French. Baguette.
        thread.start_new_thread(send_status, (current_timestamp, energy, distance, strikes_since_last_alert + 1))
        strikes_since_last_alert = 0
        last_alert = current_timestamp
    # If no strike has been detected for the last hour, reset the strikes_since_last_alert (consider storm finished)
    if (current_timestamp - last_alert).seconds > 1800 and last_alert != datetime.min:
        #thread.start_new_thread(send_tweet, (
        #        "\o/ Orage terminé. Aucun nouvel éclair détecté depuis 1/2h.",))
        strikes_since_last_alert = 0
        last_alert = datetime.min


# Use a software Pull-Down on interrupt pin
GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
sensor.set_mask_disturber(False)

# post setup data, once per run
settingsData = OrderedDict()
min_strikes = sensor.get_min_strikes()
indoors = sensor.get_indoors()
disp_lco = sensor.get_disp_lco()
noise_floor = sensor.get_noise_floor()
thread.start_new_thread(send_settings, (min_strikes, indoors, disp_lco, noise_floor))

# now configure for run in main loop
GPIO.add_event_detect(pin, GPIO.RISING, callback=handle_interrupt)
print("Waiting for lightning - or at least something that looks like it")

try:
    while True:
        # Read/clear the sensor data every 10s in case we missed an interrupt (interrupts happening too fast ?)
        time.sleep(10)
        handle_interrupt(pin)
finally:
    # cleanup used pins... just because we like cleaning up after us
    GPIO.cleanup()
