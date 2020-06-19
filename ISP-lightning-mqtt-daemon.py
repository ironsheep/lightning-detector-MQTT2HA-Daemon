#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from RPi_AS3935 import RPi_AS3935
import RPi.GPIO as GPIO
import _thread
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

script_version = "1.1.0"
project_name = 'lightning-detector-MQTT2HA-Daemon'
project_url = 'https://github.com/ironsheep/lightning-detector-MQTT2HA-Daemon'

if False:
    # will be caught by python 2.7 to be illegal syntax
    print_line('Sorry, this script requires a python3 runtime environment.', file=sys.stderr)


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
        print_line('')  # blank line?!
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

# Read/clear the detector data every 10s in case we missed an interrupt (interrupts happening too fast ?)
sleep_period = config['Daemon'].getint('period', 10)


# Script Accumulation and reporting behavior
min_period_in_minutes = 2
max_period_in_minutes = 10
default_period_in_minutes = 5   # [2-10]
period_in_minutes = int(config['Behavior'].get('period_in_minutes', default_period_in_minutes))

min_number_of_rings = 3 
max_number_of_rings = 7
default_number_of_rings = 5 # [3-7]
number_of_rings = int(config['Behavior'].get('number_of_rings', default_number_of_rings))

val_distance_as_km = 'km'
val_distance_as_mi = 'km'
default_distance_as = val_distance_as_km  # [km|mi]
distance_as = config['Behavior'].get('distance_as', default_distance_as)


# GPIO pin used for interrupts
#  I2c = GPIO2/pin3/SDA, GPIO3/pin5/SCL
#  SPI = GPI10/pin19/MOSI, GPIO9/pin21/MISO, GPIO11/pin23/SCLK, GPIO8/pin24/CE0, GPIO7/pin26/CE1
default_intr_pin = 17   # any GPIO pin not used for comms with chip
intr_pin = int(config['Sensor'].get('intr_pin', default_intr_pin))

default_i2c_bus = 1
default_i2c_address = 0x03

i2c_bus = int(config['Sensor'].get('i2c_bus', default_i2c_bus))
i2c_address = int(config['Sensor'].get('i2c_address', default_i2c_address))

default_detector_afr_gain_indoor = True
detector_afr_gain_indoor = config['Sensor'].get('detector_afr_gain_indoor', default_detector_afr_gain_indoor)

# noise_floor (0-7)
default_detector_noise_floor = 1
detector_noise_floor = int(config['Sensor'].get('detector_noise_floor', default_detector_noise_floor))

# number of strikes (def: 5, value 1,5,9,16), then are fired normally.
default_detector_min_strikes = 5
detector_min_strikes = int(config['Sensor'].get('detector_min_strikes', default_detector_min_strikes))

#  FIXME: UNONE - let's add value VALIDATION!!!

# Check configuration
#
#
if (period_in_minutes < min_period_in_minutes) or (period_in_minutes > max_period_in_minutes):
    print_line('ERROR: Invalid "period_in_minutes" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_period_in_minutes, max_period_in_minutes), error=True, sd_notify=True)
    sys.exit(1)   

if (number_of_rings < min_number_of_rings) or (number_of_rings > max_number_of_rings):
    print_line('ERROR: Invalid "number_of_rings" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_number_of_rings, max_number_of_rings), error=True, sd_notify=True)
    sys.exit(1)   

if (distance_as != val_distance_as_km) and (distance_as != val_distance_as_mi):
    print_line('ERROR: Invalid "distance_as" found in configuration file: "config.ini"! Must be ["{}" or "{}"] Fix and try again... Aborting'.format(val_distance_as_km, val_distance_as_mi), error=True, sd_notify=True)
    sys.exit(1)   

### Ensure required values within sections of our config are present
if not config['MQTT']:
    print_line('ERROR: No MQTT settings found in configuration file "config.ini"! Fix and try again... Aborting', error=True, sd_notify=True)
    sys.exit(1)


print_line('Configuration accepted', console=False, sd_notify=True)

# MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'Online'
lwt_offline_val = 'Offline'

print_line('Connecting to MQTT broker ...')
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

mqtt_client.will_set(lwt_topic, payload=lwt_offline_val, retain=True)

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
    mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=True)
    mqtt_client.loop_start()
    sleep(1.0) # some slack to establish the connection

sd_notifier.notify('READY=1')

# our lighting device
LD_TIMESTAMP = "last"
LD_ENERGY = "energy"    # 21b value unsigned
LD_DISTANCE = "distance"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km
LD_COUNT = "count"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km
LD_CURRENT_RINGS = "crings"
LD_PAST_RINGS = "prings"

LDS_MIN_STRIKES = "min_strikes" # 1,5,9,16
LDS_LOCATION = "afe_inside" # indoors, outdoors
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
    (LD_TIMESTAMP, dict(title="Last", device_class="timestamp", device_ident="yes")),
    (LD_ENERGY, dict(title="Energy")),
    (LD_DISTANCE, dict(title="Distance", unit=distance_as)),
    (LD_COUNT, dict(title="Count")),
    (LD_CURRENT_RINGS, dict(title="Current Rings", no_title_prefix="yes", json_values="yes")),
    (LD_PAST_RINGS, dict(title="Past Rings", no_title_prefix="yes", json_values="yes"))
])

print_line('Announcing Lightning Detection device to MQTT broker for auto-discovery ...')
#for [sensor_name, sensor_dict] in detectorValues.items():
base_topic = '{}/sensor/{}'.format(base_topic, sensor_name.lower())
settings_topic = '{}/settings'.format(base_topic)
crings_topic = '{}/crings'.format(base_topic)    # vs. LWT
prings_topic = '{}/prings'.format(base_topic)    # vs. LWT

state_topic_rel = '{}/detect'.format('~')
state_topic = '{}/detect'.format(base_topic)

activity_topic_rel = '{}/status'.format('~')     # vs. LWT
activity_topic = '{}/status'.format(base_topic)    # vs. LWT

command_topic_rel = '~/set'


for [sensor, params] in detectorValues.items():
    discovery_topic = 'homeassistant/sensor/{}/{}/config'.format(sensor_name.lower(), sensor)
    payload = OrderedDict()
    if 'no_title_prefix' in params:
        payload['name'] = "{}".format(params['title'].title())
    else:
        payload['name'] = "{} {}".format(sensor_name.title(), params['title'].title())
    payload['uniq_id'] = "{}_{}".format(uniqID, sensor.lower())
    if 'device_class' in params:
        payload['dev_cla'] = params['device_class']
    if 'unit' in params:
        payload['unit_of_measurement'] = params['unit']
    if 'json_values' in params:
        payload['stat_t'] = "~/{}".format(sensor)
        payload['val_tpl'] = "{{{{ value_json.{}.timestamp }}}}".format(sensor)
    else:
        payload['stat_t'] = state_topic_rel
        payload['val_tpl'] = "{{{{ value_json.{} }}}}".format(sensor)
    payload['~'] = base_topic
    payload['pl_avail'] = lwt_online_val
    payload['pl_not_avail'] = lwt_offline_val
    payload['avty_t'] = activity_topic_rel
    if 'json_values' in params:
        payload['json_attr_t'] = "~/{}".format(sensor)
        payload['json_attr_tpl'] = '{{{{ value_json.{} | tojson }}}}'.format(sensor)
    if 'device_ident' in params:
        payload['dev'] = {
                'identifiers' : ["{}".format(uniqID)],
                'connections' : [["mac", mac.lower()], [interface, ipaddr]],
                'manufacturer' : '(Austria Micro Systems) ams AG',
                'name' : sensor_name,
                'model' : 'Lightning Detector (AS3935)',
                'sw_version': "v{}".format(script_version)
        }
    else:
         payload['dev'] = {
                'identifiers' : ["{}".format(uniqID)],
         }
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)



# Initialize GPIO
GPIO.setmode(GPIO.BCM)

# pin used for interrupts

# Rev. 1 Raspberry Pis should leave bus set at 0, while rev. 2 Pis should set
# bus equal to 1. The address should be changed to match the address of the
# detector IC.
print_line('I2C configuration addr={} - bus={}'.format(i2c_address, i2c_bus))

detector = RPi_AS3935.RPi_AS3935(i2c_address, i2c_bus)
# Indoors = more sensitive (can miss very strong lightnings)
# Outdoors = less sensitive (can miss far away lightnings)
detector.set_indoors(detector_afr_gain_indoor)
detector.set_noise_floor(default_detector_noise_floor)
# Change this value to the tuning value for your detector
detector.calibrate(tun_cap=0x01)
# Prevent single isolated strikes from being logged => interrupts begin after 5 strikes, then are fired normally
detector.set_min_strikes(detector_min_strikes)

last_alert = datetime.min
strikes_since_last_alert = 0


# We use a function to send tweet so that we can run it in a different thread and avoid spending too much time in the
# interrupt handle
#def send_tweet(tweet):
#   #api.update_status(tweet)

def send_settings(minStrikes, isIndoors, isDispLco, noiseFloor):
    settingsData = OrderedDict()
    settingsData[LDS_MIN_STRIKES] = minStrikes
    settingsData[LDS_LOCATION] = isIndoors
    settingsData[LDS_LCO_ON_INT] = isDispLco
    settingsData[LDS_NOISE_FLOOR] = noiseFloor
    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(settings_topic, json.dumps(settingsData)))
    mqtt_client.publish('{}'.format(settings_topic), json.dumps(settingsData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function

def send_status(timestamp, energy, distance, strikeCount):
    statusData = OrderedDict()
    statusData[LD_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
    statusData[LD_ENERGY] = energy
    statusData[LD_DISTANCE] = distance
    statusData[LD_COUNT] = strikeCount

    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(state_topic, json.dumps(statusData)))
    mqtt_client.publish('{}'.format(state_topic), json.dumps(statusData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function

# Interrupt handler
def handle_interrupt(channel):
    global last_alert
    global strikes_since_last_alert
    global detector
    current_timestamp = datetime.now()
    sleep(0.003)
    reason = detector.get_interrupt()
    if reason == 0x01:
        print_line("Noise level too high - adjusting")
        detector.raise_noise_floor()
    elif reason == 0x04:
        print_line("Disturber detected. Masking subsequent disturbers")
        detector.set_mask_disturber(True)
    elif reason == 0x08:
        print_line("We sensed lightning! (%s)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'))
        if (current_timestamp - last_alert).seconds < 3:
            print_line("Last strike is too recent, incrementing counter since last alert.")
            strikes_since_last_alert += 1
            return
        distance = detector.get_distance()
        energy = detector.get_energy()
        print_line("Energy: " + str(energy) + " - distance: " + str(distance) + "km")
        # Yes, it tweets in French. Baguette.
        _thread.start_new_thread(send_status, (current_timestamp, energy, distance, strikes_since_last_alert + 1))
        strikes_since_last_alert = 0
        last_alert = current_timestamp
    # If no strike has been detected for the last hour, reset the strikes_since_last_alert (consider storm finished)
    if (current_timestamp - last_alert).seconds > 1800 and last_alert != datetime.min:
        #_thread.start_new_thread(send_tweet, (
        #        "\o/ Orage terminé. Aucun nouvel éclair détecté depuis 1/2h.",))
        strikes_since_last_alert = 0
        last_alert = datetime.min


# Use a software Pull-Down on interrupt pin
pin = int(intr_pin)
GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
detector.set_mask_disturber(False)

# post setup data, once per run
settingsData = OrderedDict()
min_strikes = detector.get_min_strikes()
indoors = detector.get_indoors()
disp_lco = detector.get_disp_lco()
noise_floor = detector.get_noise_floor()
_thread.start_new_thread(send_settings, (min_strikes, indoors, disp_lco, noise_floor))

# now configure for run in main loop
GPIO.add_event_detect(pin, GPIO.RISING, callback=handle_interrupt)
print_line("Waiting for lightning - or at least something that looks like it")

try:
    while True:
        # Read/clear the detector data every 10s in case we missed an interrupt (interrupts happening too fast ?)
        sleep(sleep_period)
        handle_interrupt(pin)
finally:
    # cleanup used pins... just because we like cleaning up after us
    GPIO.cleanup()
