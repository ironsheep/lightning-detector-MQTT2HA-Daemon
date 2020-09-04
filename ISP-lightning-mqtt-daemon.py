#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import RPi.GPIO as GPIO
import _thread
from datetime import datetime
from tzlocal import get_localzone

import threading
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

script_version = "2.2.0"
script_name = 'ISP-lightning-mqtt-daemon.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'lightning-detector-MQTT2HA-Daemon'
project_url = 'https://github.com/ironsheep/lightning-detector-MQTT2HA-Daemon'

# we'll use this throughout
local_tz = get_localzone()

if False:
    # will be caught by python 2.7 to be illegal syntax
    print_line('Sorry, this script requires a python3 runtime environment.', file=sys.stderr)

# Argparse
opt_debug = False
opt_verbose = False

# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()

# Logging function
def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False, console=True, sd_notify=False, log=False):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        elif info or verbose:
            if opt_verbose:
                print(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW  + '- ' + '{}'.format(text) + Style.RESET_ALL)
        elif debug:
            if opt_debug:
                print(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
            else:
                print(Fore.YELLOW + '[{}] '.format(timestamp) + Fore.YELLOW  + '- ' + '{}'.format(text) + Style.RESET_ALL)
        elif log:
            if opt_debug:
                print(Fore.MAGENTA + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
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

# Argparse
parser = argparse.ArgumentParser(description=script_info, epilog='For further details see: ' + project_url)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
parser.add_argument("-d", "--debug", help="show debug output", action="store_true")
parser.add_argument("-f", '--test_filename', help='load detections from test filename instead of using sensor', default='')
parser.add_argument("-s", '--test_scale', help='adjust test speed to run a ?x [Default 1x]', default='1')
parser.add_argument("-t", "--calc_tuning_cap", help="run routine to calclulate tuning cap value for your board", action="store_true")
parser.add_argument("-c", '--config_dir', help='set directory where config.ini is located', default=sys.path[0])
parse_args = parser.parse_args()

config_dir = parse_args.config_dir
test_filename = parse_args.test_filename
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_testing = len(test_filename) > 0
opt_scale = int(parse_args.test_scale)
opt_calc_tuning_cap = parse_args.calc_tuning_cap

disable_mqtt = False
print_line(script_info, info=True)
if opt_verbose:
    print_line('Verbose enabled', info=True)
if opt_debug:
    print_line('Debug enabled', debug=True)
if opt_testing:
    print_line('* Mode TESTING... @ {}x speed'.format(opt_scale))
if opt_calc_tuning_cap:
    print_line('* Mode: Calculate Tuning Cap value and exit')
    disable_mqtt = True

# -----------------------------------------------------------------------------
#  MQTT handlers
# -----------------------------------------------------------------------------

# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
mqtt_client_connected = False
print_line('* init mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)

# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
def on_connect(client, userdata, flags, rc):
    global mqtt_client_connected
    if rc == 0:
        print_line('MQTT connection established', console=True, sd_notify=True)
        print_line('')  # blank line?!
        mqtt_client_connected = True
        print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
    else:
        print_line('Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True)
        print_line('MQTT Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True, sd_notify=True)
        mqtt_client_connected = False   # technically NOT useful but readying possible new shape...
        print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
        #kill main thread
        os._exit(1)

def on_publish(client, userdata, mid):
    #print_line('Data successfully published.')
    pass

def on_log(client, userdata, level, buf):
    #print_line('* Data successfully published.')
    print_line("log: {}".format(buf), debug=True, log=True)


# Load configuration file
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

min_end_storm_after_minutes = 10
max_end_storm_after_minutes = 60
default_end_storm_after_minutes = 30   # [10-60]
end_storm_after_minutes = int(config['Behavior'].get('end_storm_after_minutes', default_end_storm_after_minutes))

val_distance_as_km = 'km'
val_distance_as_mi = 'mi'
default_distance_as = val_distance_as_km  # [km|mi]
distance_as = config['Behavior'].get('distance_as', default_distance_as)


# GPIO pin used for interrupts
#  I2c = GPIO2/pin3/SDA, GPIO3/pin5/SCL
#  SPI = GPI10/pin19/MOSI, GPIO9/pin21/MISO, GPIO11/pin23/SCLK, GPIO8/pin24/CE0, GPIO7/pin26/CE1
val_interface_type_i2c = 'I2C'
val_interface_type_spi = 'SPI'
default_interface_type = val_interface_type_i2c
interface_type = config['Sensor'].get('sensor_attached', default_interface_type).upper()

default_intr_pin = 17   # any GPIO pin not used for comms with chip
intr_pin = int(config['Sensor'].get('intr_pin', default_intr_pin))

default_i2c_bus = '1'
default_i2c_address = '0x03'

config_i2c_bus = config['Sensor'].get('i2c_bus', default_i2c_bus)
if config_i2c_bus.startswith('0x'):
    i2c_bus = int(config_i2c_bus,16)
else:
    i2c_bus = int(config_i2c_bus)

config_i2c_address = config['Sensor'].get('i2c_address', default_i2c_address)
if config_i2c_address.startswith('0x'):
    i2c_address = int(config_i2c_address,16)
else:
    i2c_address = int(config_i2c_address)

default_spi_bus = '0'
default_spi_device = '0'

config_spi_bus = config['Sensor'].get('spi_bus', default_spi_bus)
if config_spi_bus.startswith('0x'):
    spi_bus = int(config_spi_bus,16)
else:
    spi_bus = int(config_spi_bus)

config_spi_device = config['Sensor'].get('spi_device', default_spi_device)
if config_spi_device.startswith('0x'):
    spi_device = int(config_spi_device,16)
else:
    spi_device = int(config_spi_device)

min_tuning_capacitor = 0
max_tuning_capacitor = 15
default_tuning_capacitor = '1'
config_tuning_capacitor = config['Sensor'].get('tuning_capacitor', default_tuning_capacitor)
if config_tuning_capacitor.startswith('0x'):
    tuning_capacitor = int(config_tuning_capacitor,16)
else:
    tuning_capacitor = int(config_tuning_capacitor)

default_detector_afr_gain_indoor = True
detector_afr_gain_indoor = config['Sensor'].get('detector_afr_gain_indoor', default_detector_afr_gain_indoor)

# noise_floor (0-7)
default_detector_noise_floor = 1
detector_noise_floor = int(config['Sensor'].get('detector_noise_floor', default_detector_noise_floor))

# number of strikes (def: 5, value 1,5,9,16), then are fired normally.
default_detector_min_strikes = 5
detector_min_strikes = int(config['Sensor'].get('detector_min_strikes', default_detector_min_strikes))

# Check configuration
#
if (tuning_capacitor < min_tuning_capacitor) or (tuning_capacitor > max_tuning_capacitor):
    print_line('ERROR: Invalid "tuning_capacitor" value found in configuration file: "config.ini"! Must be [{} - {}] Fix and try again... Aborting'.format(min_tuning_capacitor, max_tuning_capacitor), error=True, sd_notify=True)
    sys.exit(1)

if (interface_type != val_interface_type_i2c) and (interface_type != val_interface_type_spi):
    print_line('ERROR: Invalid "sensor_attached" value found in configuration file: "config.ini"! Must be [{} or {}] Fix and try again... Aborting'.format(val_interface_type_i2c, val_interface_type_spi), error=True, sd_notify=True)
    sys.exit(1)

if (period_in_minutes < min_period_in_minutes) or (period_in_minutes > max_period_in_minutes):
    print_line('ERROR: Invalid "period_in_minutes" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_period_in_minutes, max_period_in_minutes), error=True, sd_notify=True)
    sys.exit(1)

if (end_storm_after_minutes < min_end_storm_after_minutes) or (end_storm_after_minutes > max_end_storm_after_minutes):
    print_line('ERROR: Invalid "end_storm_after_minutes" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_end_storm_after_minutes, max_end_storm_after_minutes), error=True, sd_notify=True)
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
print_line('* Sensor on {} bus'.format(interface_type))

sensor_using_spi = False
if interface_type == 'SPI':
    sensor_using_spi = True

# -----------------------------------------------------------------------------
#  timer and timer funcs for ALIVE MQTT Notices handling
# -----------------------------------------------------------------------------

ALIVE_TIMOUT_IN_SECONDS = 60

def publishAliveStatus():
    print_line('- SEND: yes, still alive -', debug=True)
    mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)

def aliveTimeoutHandler():
    print_line('- MQTT TIMER INTERRUPT -', debug=True)
    _thread.start_new_thread(publishAliveStatus, ())
    startAliveTimer()

def startAliveTimer():
    global aliveTimer
    global aliveTimerRunningStatus
    stopAliveTimer()
    aliveTimer = threading.Timer(ALIVE_TIMOUT_IN_SECONDS, aliveTimeoutHandler)
    aliveTimer.start()
    aliveTimerRunningStatus = True
    print_line('- started MQTT timer - every {} seconds'.format(ALIVE_TIMOUT_IN_SECONDS), debug=True)

def stopAliveTimer():
    global aliveTimer
    global aliveTimerRunningStatus
    aliveTimer.cancel()
    aliveTimerRunningStatus = False
    print_line('- stopped MQTT timer', debug=True)

def isAliveTimerRunning():
    global aliveTimerRunningStatus
    return aliveTimerRunningStatus

# our ALIVE TIMER
aliveTimer = threading.Timer(ALIVE_TIMOUT_IN_SECONDS, aliveTimeoutHandler)
# our BOOL tracking state of ALIVE TIMER
aliveTimerRunningStatus = False


# -----------------------------------------------------------------------------
#  MQTT setup and startup
# -----------------------------------------------------------------------------

# MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'Online'
lwt_offline_val = 'Offline'

if not disable_mqtt:
    print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish
mqtt_client.on_log = on_log

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

if not disable_mqtt:
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
        mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)
        mqtt_client.loop_start()

        while mqtt_client_connected == False: #wait in loop
            print_line('* Wait on mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
            sleep(1.0) # some slack to establish the connection

        startAliveTimer()

    sd_notifier.notify('READY=1')

# -----------------------------------------------------------------------------
#  Perform our MQTT Discovery Announcement...
# -----------------------------------------------------------------------------

# our lighting device
LD_TIMESTAMP = "last"
LD_ENERGY = "energy"    # 21b value unsigned
LD_DISTANCE = "distance"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km
LD_COUNT = "count"   # 5b value: 1=overhead, 63(0x3f)=out-of-range, 2-62 dist in km
LD_CURRENT_RINGS = "crings"
LD_PAST_RINGS = "prings"
LD_SETTINGS = "settings"

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
#  table of key items to publish:
detectorValues = OrderedDict([
    (LD_TIMESTAMP, dict(title="Last", device_class="timestamp", device_ident="Lightning Detector")),
    (LD_ENERGY, dict(title="Energy")),
    (LD_DISTANCE, dict(title="Distance", unit=distance_as)),
    (LD_COUNT, dict(title="Count")),
    (LD_SETTINGS, dict(title="Detector Settings", device_class="timestamp", no_title_prefix="yes", json_values="yes")),
    (LD_CURRENT_RINGS, dict(title="Current RingSet", device_class="timestamp", no_title_prefix="yes", json_values="yes")),
    (LD_PAST_RINGS, dict(title="Past RingSet", device_class="timestamp", no_title_prefix="yes", json_values="yes"))
])

if not disable_mqtt:
    print_line('Announcing Lightning Detection device to MQTT broker for auto-discovery ...')

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
                'name' : params['device_ident'],
                'model' : 'Lightning Detector (AS3935)',
                'sw_version': "v{}".format(script_version)
        }
    else:
         payload['dev'] = {
                'identifiers' : ["{}".format(uniqID)],
         }

    if not disable_mqtt:
        mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)


# -----------------------------------------------------------------------------
#  timer and timer funcs for period handling
# -----------------------------------------------------------------------------

TIMER_INTERRUPT = (-1)
TEST_INTERRUPT = (-2)

def periodTimeoutHandler():
    print_line('- PERIOD TIMER INTERRUPT -', debug=True)
    handle_interrupt(TIMER_INTERRUPT) # '0' means we have a timer interrupt!!!
    startPeriodTimer()

def startPeriodTimer():
    global endPeriodTimer
    global periodTimeRunningStatus
    stopPeriodTimer()
    endPeriodTimer = threading.Timer(period_in_minutes * 60.0, periodTimeoutHandler)
    endPeriodTimer.start()
    periodTimeRunningStatus = True
    print_line('- started PERIOD timer - every {} seconds'.format(period_in_minutes * 60.0), debug=True)

def stopPeriodTimer():
    global endPeriodTimer
    global periodTimeRunningStatus
    endPeriodTimer.cancel()
    periodTimeRunningStatus = False
    print_line('- stopped PERIOD timer', debug=True)

def isPeriodTimerRunning():
    global periodTimeRunningStatus
    return periodTimeRunningStatus



# our TIMER
endPeriodTimer = threading.Timer(period_in_minutes * 60.0, periodTimeoutHandler)
# our BOOL tracking state of TIMER
periodTimeRunningStatus = False

# -----------------------------------------------------------------------------
#  MQTT Transmit Helper Routines
# -----------------------------------------------------------------------------
LDS_TIMESTAMP = "timestamp"
LDS_CAT_HARDWARE = "hardware"
LDS_MIN_STRIKES = "min_strikes" # 1,5,9,16
LDS_LOCATION = "afe_inside" # indoors, outdoors
LDS_LCO_ON_INT = "disp_lco" # T/F where T means LCO is transmitting on Intr pin (can't detect when this is true)
LDS_NOISE_FLOOR = "noise_floor" # [0-7]

LDS_CAT_SCRIPT = "script"
LDS_PERIOD_IN_MINUTES = "period_minutes"
LDS_END_STORM_IN_MINUTES = "end_minutes"
LDS_NUMBER_RINGS = "number_rings"
LDS_DISTANCE_UNITS = "distance_units"

def send_settings(minStrikes, isIndoors, isDispLco, noiseFloor):
    topSettingsData = OrderedDict()

    current_timestamp = datetime.now(local_tz)
    settingsData[LDS_TIMESTAMP] = current_timestamp.astimezone().replace(microsecond=0).isoformat()

    hardwareData = OrderedDict()
    hardwareData[LDS_MIN_STRIKES] = minStrikes
    hardwareData[LDS_LOCATION] = isIndoors
    hardwareData[LDS_LCO_ON_INT] = isDispLco
    hardwareData[LDS_NOISE_FLOOR] = noiseFloor

    settingsData[LDS_CAT_HARDWARE] = hardwareData

    scriptData = OrderedDict()
    scriptData[LDS_PERIOD_IN_MINUTES] = period_in_minutes
    scriptData[LDS_END_STORM_IN_MINUTES] = end_storm_after_minutes
    scriptData[LDS_NUMBER_RINGS] = number_of_rings
    scriptData[LDS_DISTANCE_UNITS] = distance_as

    settingsData[LDS_CAT_SCRIPT] = scriptData

    topSettingsData['settings'] = settingsData

    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(settings_topic, json.dumps(topSettingsData)))
    mqtt_client.publish('{}'.format(settings_topic), json.dumps(topSettingsData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function

def send_status(timestamp, energy, distance, strikeCount):
    statusData = OrderedDict()
    statusData[LD_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
    statusData[LD_ENERGY] = energy
    if distance == None:
        statusData[LD_DISTANCE] = 'out of range'
    else:
        statusData[LD_DISTANCE] = distance
    statusData[LD_COUNT] = strikeCount

    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(state_topic, json.dumps(statusData)))
    mqtt_client.publish('{}'.format(state_topic), json.dumps(statusData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function


# -----------------------------------------------------------------------------
#  Strike Accumulator Routines
# -----------------------------------------------------------------------------


# ring keys
STRIKE_COUNT_KEY = 'count'
DISTANCE_KEY = 'distance_km'
FROM_SCALED_KEY = 'from_units'
TO_SCALED_KEY = 'to_units'
ENERGY_KEY = 'energy'
TOTAL_ENERGY_KEY = 'total_energy'   #internal
ACCUM_COUNT_KEY = 'accumulated_count'   #internal
# top keys
RING_PREFIX_KEY = 'ring'
UNITS_KEY = 'units'
PERIOD_IN_MINUTES_KEY = 'period_minutes'
TIMESTAMP_KEY = 'timestamp'
LAST_DETECT_KEY = 'last'
FIRST_DETECT_KEY = 'first'
STORM_LAST_DETECT_KEY = 'storm_last'
STORM_FIRST_DETECT_KEY = 'storm_first'
STORM_END_MINUTES_KEY = 'end_minutes'
OUT_OF_RANGE_KEY = 'out_of_range'
RING_COUNT_KEY = 'ring_count'
RING_WIDTH_KEY = 'ring_width_km'


# master list names
CURR_RINGS_KEY = 'crings'
PAST_RINGS_KEY = 'prings'

# number of distance values
MAX_DISTANCE_VALUES = 14

distanceValueToIndexList = list(( 1, 5, 6, 8, 10, 12, 14, 17, 20, 24, 27, 31, 34, 37, 40, 63 ))
if len(distanceValueToIndexList) != 1 + MAX_DISTANCE_VALUES + 1:
      raise TypeError("[CODE] the distanceValueToIndexList must have 16 entries!!  Aborting!")

# calculate the index to the indexSet we need based on current settings and get the list
binIndexList = number_of_rings - min_number_of_rings
binIndexesForThisRun = list(( 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 ))
# ensure the list is proper sized

if len(binIndexesForThisRun) != MAX_DISTANCE_VALUES:
      raise TypeError("a bin index-set must have 14 entries!!  Aborting!")

#  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, OOR (out of range)
#
#  1, 5, 6, 8, 10, 12, 14, 17, 20, 24, 27, 31, 34, 37, 40, 63   # value from sensor
#  0, 1, 2, 3, 4,  5,  6,  7,  8,  9,  10, 11, 12, 13, 14, 15   # our internal value
#
#  3-7 bins + overhead + OOR

accumulatedDetections = []  # sliding window of period strikes, new on front tail evaporates at end of period
accumulatorBins = []        # our rings (bins)
accumulatorLastStrike = ''  # earliest detection timestamp (this period)
accumulatorFirstStrike = ''  # latest detection timestamp (this period)
accumulatorStormLastStrike = ''  # earliest detection timestamp (whole storm)
accumulatorStormFirstStrike = ''  # latest detection timestamp (whole storm)
accumulatorOutOfRangeCount = 0
accumulatorBinDistances = []


def resetStormTracking():
    global accumulatorStormLastStrike
    global accumulatorStormFirstStrike
    accumulatorStormLastStrike = ''
    accumulatorStormFirstStrike = ''
    print_line('Removing all storm knowledge (reset)', debug=True)

def resetAccumulatorToEmpty():
    global accumulatorBins
    global accumulatorLastStrike
    global accumulatorFirstStrike
    global accumulatorOutOfRangeCount
    # first empty our list if it wasn't
    accumulatorBins.clear
    # allocate an empty dictionary for each bin we need 0 + 1-[3-7] = [4-8 bins]
    accumulatorBins = list( {} for i in list(range(number_of_rings + 1)) )  # n rings + 1 for "overhead" (out of range(63) is just counted)
    # and reset these values
    accumulatorOutOfRangeCount = 0
    accumulatorLastStrike = ''
    accumulatorFirstStrike = ''

def calculate_ring_widths():
    global accumulatorBinDistances
    # first empty our list if it wasn't
    accumulatorBinDistances.clear
    # place a zero for each bin we need
    accumulatorBinDistances = list( 0 for i in list(range(number_of_rings + 1)) )  # n rings + 1 for "overhead" (out of range(63) is just counted)
    # FIXME: UNDONE now let's calculate the value for each bin
    #  ring 1 starts at 5km so subtract that initially but add it back in for each except overhead
    binWidth = (40 - 5) / number_of_rings
    for ringIndex in range(number_of_rings + 1):
        if ringIndex == 0:
            accumulatorBinDistances[ringIndex] = 0
        else:
            accumulatorBinDistances[ringIndex] = (binWidth * (ringIndex - 1)) + 5
    # now set up distance to bin index array lookup table
    for distanceIndex in range(MAX_DISTANCE_VALUES):    # 0-13
        reportedDistance = distanceValueToIndexList[distanceIndex + 1]    # [5-40]
        binIndex = 0
        for ringIndex in range(number_of_rings + 1):    # [0,1-7 for 7 rings]
            accumulatorDistance = accumulatorBinDistances[ringIndex]
            if accumulatorDistance <= reportedDistance:
                binIndex = ringIndex
            else:
                break   # stop, we have our answer
        binIndexesForThisRun[distanceIndex] = binIndex
    #print('- distanceValueToIndexList "{}"'.format(distanceValueToIndexList))
    #print('- accumulatorBinDistances "{}"'.format(accumulatorBinDistances))
    #print('- binIndexesForThisRun "{}"'.format(binIndexesForThisRun))

def binIndexFromDistance(distance):
    try:
        testDistance = distance
        if distance == None:
            testDistance = 63
        # given distance determine index value for it... NOTE: 1=idx-0 and 63=idx-15
        desiredBinIndex = distanceValueToIndexList.index(testDistance)
        # if we have 1-14 let's translate it into a ring index value [1-[3-7]]
        if desiredBinIndex > 0 and desiredBinIndex < 15:
            desiredBinIndex = binIndexesForThisRun[desiredBinIndex - 1]
    except ValueError:
        raise TypeError("[CODE] WHAT?? Unexpected Value from detector[{}]!!  Aborting!".format(distance))
    return desiredBinIndex

    # =========================================================================
    #  we are moving to a new accumulation strategy
    #   instead of accumulating directly into the bins we are going to keep
    #   a moving window of strikes and push this moving window into the bins
    #   only when we need to report the bin-set
    #
    #
    # -------------------------------------------------------------------------
def ageDetections(accumulatedDetectionsList, period_in_minutes):
    filteredList = accumulatedDetectionsList.copy()
    timeNow = datetime.now(local_tz)
    # our TUPLE is: (timestamp, energy, distance, strikeCount)
    #   chase from oldest to youngest...
    removed_count = 0
    for currDetection in accumulatedDetectionsList:
        detectionTimestamp = currDetection[0]
        timeDifference = timeNow - detectionTimestamp
        detectionAgeInMinutes = timeDifference.seconds / 60
        # if too old remove it then look at next
        if detectionAgeInMinutes > period_in_minutes:
            filteredList.remove(currDetection)
            removed_count += 1
        else:
            # this one is young enough so no point in checking any more...
            break

    orig_count = len(accumulatedDetectionsList)
    new_count = len(filteredList)

    print_line('adjusted detection set: enter with {} , leave with {}, removed {}'.format(orig_count, new_count, removed_count), debug=True)
    return filteredList

def accumulate(timestamp, energy, distance, strikeCount):
    global accumulatedDetections
    global accumulatorStormLastStrike
    global accumulatorStormFirstStrike

    # append this to our list then remove old (outside of period) detections from the list
    accumulatedDetections.append( (timestamp, energy, distance, strikeCount) )

    if(accumulatorStormFirstStrike == ''):
        accumulatorStormFirstStrike = timestamp

    accumulatorStormLastStrike = timestamp

    accumulatedDetections = ageDetections(accumulatedDetections, period_in_minutes)

def removeOldDetections():
    global accumulatedDetections
    accumulatedDetections = ageDetections(accumulatedDetections, period_in_minutes)
    print_line('Removing old detections from set', debug=True)


def getDictionaryForAccumulatorNamed(dictionaryName):
    global accumulatorBins
    global accumulatorLastStrike
    global accumulatorFirstStrike
    global accumulatorStormLastStrike
    global accumulatorStormFirstStrike
    global accumulatorOutOfRangeCount
    # build a past dictionary and send it
    tmpRingsDict = OrderedDict()

    current_timestamp = datetime.now(local_tz)
    tmpRingsDict[TIMESTAMP_KEY] = current_timestamp.astimezone().replace(microsecond=0).isoformat()
    if accumulatorLastStrike != '':
        tmpRingsDict[LAST_DETECT_KEY] = accumulatorLastStrike.astimezone().replace(microsecond=0).isoformat()
    if accumulatorFirstStrike != '':
        tmpRingsDict[FIRST_DETECT_KEY] = accumulatorFirstStrike.astimezone().replace(microsecond=0).isoformat()
    if accumulatorStormLastStrike != '':
        tmpRingsDict[STORM_LAST_DETECT_KEY] = accumulatorStormLastStrike.astimezone().replace(microsecond=0).isoformat()
    if accumulatorStormFirstStrike != '':
        tmpRingsDict[STORM_FIRST_DETECT_KEY] = accumulatorStormFirstStrike.astimezone().replace(microsecond=0).isoformat()
    tmpRingsDict[STORM_END_MINUTES_KEY] = end_storm_after_minutes
    tmpRingsDict[PERIOD_IN_MINUTES_KEY] = period_in_minutes
    tmpRingsDict[UNITS_KEY] = distance_as
    tmpRingsDict[OUT_OF_RANGE_KEY] = accumulatorOutOfRangeCount
    tmpRingsDict[RING_COUNT_KEY] = number_of_rings
    tmpRingsDict[RING_WIDTH_KEY] = round((40 - 5) / number_of_rings, 1)

    if distance_as == val_distance_as_km:
        distance_multiplier = 1.0
        minus_one_value = 1.0 / 10.0
    else:
        distance_multiplier = 0.621371
        # miles are shown in tenths
        minus_one_value = distance_multiplier / 10.0

    for ringIndex in range(number_of_rings + 1):
        binForThisRing = accumulatorBins[ringIndex]
        singleRingData = OrderedDict()
        if STRIKE_COUNT_KEY in binForThisRing:
            singleRingData[STRIKE_COUNT_KEY] = binForThisRing[STRIKE_COUNT_KEY]
        else:
            singleRingData[STRIKE_COUNT_KEY] = 0
        # dstance in km
        singleRingData[DISTANCE_KEY] = round(accumulatorBinDistances[ringIndex], 1)
        # distance in desired units
        fromValue = accumulatorBinDistances[ringIndex] * distance_multiplier
        if ringIndex < number_of_rings:
            toValue = (accumulatorBinDistances[ringIndex + 1] * distance_multiplier) - minus_one_value
        else:
            toValue = 40 * distance_multiplier
        # round the following to 1 decimal place...
        singleRingData[FROM_SCALED_KEY] = round(fromValue, 1)
        singleRingData[TO_SCALED_KEY] = round(toValue, 1)
        if ENERGY_KEY in binForThisRing:
            singleRingData[ENERGY_KEY] = binForThisRing[ENERGY_KEY]
        else:
            singleRingData[ENERGY_KEY] = 0
        ringName = "ring{}".format(ringIndex)
        tmpRingsDict[ringName] = singleRingData

    topRingsData = OrderedDict()
    topRingsData[dictionaryName] = tmpRingsDict
    return topRingsData

def loadDetectionsIntoBins():
    global accumulatorBins
    global accumulatorLastStrike
    global accumulatorFirstStrike
    global accumulatorOutOfRangeCount
    global accumulatedDetections

    # reset the current
    resetAccumulatorToEmpty()

    # our TUPLE is: (timestamp, energy, distance, strikeCount)
    for currDetection in accumulatedDetections:
        timestamp = currDetection[0]
        energy = currDetection[1]
        distance = currDetection[2]
        strikeCount = currDetection[3]

        # place earliest detection here
        if accumulatorFirstStrike == '':
            accumulatorFirstStrike = timestamp

        # place latest detection here
        accumulatorLastStrike = timestamp

        # convert distance to bin index:
        #   NOTE: 0 is overhead while 15 is 'out of range'
        desiredBinIndex = binIndexFromDistance(distance)
        if desiredBinIndex == 15:   # out-of-range
            accumulatorOutOfRangeCount += 1
        else:
            desiredBin = accumulatorBins[desiredBinIndex]
            if STRIKE_COUNT_KEY in desiredBin:
                currCount = desiredBin[STRIKE_COUNT_KEY]
            else:
                currCount = 0
            if TOTAL_ENERGY_KEY in desiredBin:
                currTotalEnergy = desiredBin[TOTAL_ENERGY_KEY]
            else:
                currTotalEnergy = 0
            if ACCUM_COUNT_KEY in desiredBin:
                currAccumCount = desiredBin[ACCUM_COUNT_KEY]
            else:
                currAccumCount = 0

            currTotalEnergy += energy
            currAccumCount += 1
            currCount += strikeCount

            # real values for consumer
            desiredBin[STRIKE_COUNT_KEY] = currCount
            desiredBin[ENERGY_KEY] = int(currTotalEnergy / currAccumCount)
            # internal values so we can accumulate correctly
            desiredBin[TOTAL_ENERGY_KEY] = currTotalEnergy
            desiredBin[ACCUM_COUNT_KEY] = currAccumCount

def publishRingData(ringsData, topic):
    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, json.dumps(ringsData)))
    mqtt_client.publish('{}'.format(topic), json.dumps(ringsData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function

def report_past_accumulator(topic):
    # build a past dictionary and send it
    loadDetectionsIntoBins()
    pastRingsData = getDictionaryForAccumulatorNamed(PAST_RINGS_KEY)
    # send the data
    _thread.start_new_thread(publishRingData, (pastRingsData, topic))

def report_current_accumulator(topic):
    # build a current dictionary and send it
    loadDetectionsIntoBins()
    currRingsData = getDictionaryForAccumulatorNamed(CURR_RINGS_KEY)
    # send the data
    _thread.start_new_thread(publishRingData, (currRingsData, topic))

# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
#  Setup our INT pin (GPIO)
# -----------------------------------------------------------------------------
if opt_testing == False:
    # Initialize GPIO
    GPIO.setmode(GPIO.BCM)

    # Use a software Pull-Down on interrupt pin
    interrupt_pin = int(intr_pin)
    GPIO.setup(interrupt_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

# -----------------------------------------------------------------------------
#  Ready our AS3935 connected via SPI for use...
# -----------------------------------------------------------------------------
if opt_testing == False and sensor_using_spi:
    from AS3935.AS3935_i2c_spi import AS3935_SPI
    print_line('SPI configuration bus={} - device={}'.format(spi_bus, spi_device), verbose=True)

    detector = AS3935_SPI(interrupt_pin, spi_device, spi_bus)
    detector.max_speed_hz(1250000)  # 1,250,000 Hz (1.25 MHz)
    detector.mode(0b01)     # [CPOL=0|CPHA=1] per AS3935 doc.

# -----------------------------------------------------------------------------
#  Ready our AS3935 connected via I2c for use...
# -----------------------------------------------------------------------------
if opt_testing == False and sensor_using_spi == False:
    from AS3935.AS3935_i2c_spi import AS3935_I2C
    # Rev. 1 Raspberry Pis should leave bus set at 0, while rev. 2 Pis should set
    # bus equal to 1. The address should be changed to match the address of the
    # detector IC.
    print_line('I2C configuration bus={} - addr={}'.format(i2c_bus, i2c_address), verbose=True)

    detector = AS3935_I2C(interrupt_pin, i2c_address, i2c_bus)

# -----------------------------------------------------------------------------
#  Now just talk with our AS3935 connected via I2c or SPI
# -----------------------------------------------------------------------------

# but first, let's see if we have a communicating device!
print_line('- Testing AS3935 Communications...', debug=True)
testValue = 0x05
cooperatingDevice = True
detector.set_noise_floor(testValue)
noiseFloor = detector.get_noise_floor()
print_line('- TEST write={}, read-back={}'.format(testValue, noiseFloor), debug=True)
if noiseFloor != testValue:
    cooperatingDevice = False

testValue = 0x02    # inverted pattern
detector.set_noise_floor(testValue)
noiseFloor = detector.get_noise_floor()
print_line('- TEST write={}, read-back={}'.format(testValue, noiseFloor), debug=True)
if noiseFloor != testValue:
    cooperatingDevice = False

if not cooperatingDevice:
    print_line('* AS3925 Comms not working!  Aborting', error=True)
    #kill main thread
    os._exit(1)
else:
    print_line('* Have good comms with AS3935', verbose=True)

# reset the chip to defaults
detector.set_default_values()
# Indoors = more sensitive (can miss very strong lightnings)
# Outdoors = less sensitive (can miss far away lightnings)
detector.set_indoors(detector_afr_gain_indoor)
detector.set_noise_floor(default_detector_noise_floor)
# Tuning value for the detector
#detector.set_tune_antenna(tuning_capacitor)
print_line('* calibrate with antenna cap. set to {}'.format(hex(tuning_capacitor)), verbose=True)
detector.full_calibration(tuning_capacitor)
# Prevent single isolated strikes from being logged => interrupts begin after 5 strikes, then are fired normally
detector.set_min_strikes(detector_min_strikes)

first_alert = datetime.min
last_alert = datetime.min
strikes_since_last_alert = 0

synth_energy = 0
synth_distance = 63

# Interrupt handler
def handle_interrupt(channel):
    global first_alert
    global last_alert
    global strikes_since_last_alert
    global detector
    sourceID = "<< INTR(" + str(channel) + ")"
    current_timestamp = datetime.now(local_tz)
    if channel != TIMER_INTERRUPT:
        # ----------------------------------
        # have HARDWARE interrupt!
        sleep(0.003)
        # if we NOT testing use real hardware
        #  if we ARE testing then we just have detections!
        if opt_testing == False:
            reason = detector.get_interrupt()
        else:
            reason = 0x08

        if reason == 0x01:
            print_line(sourceID + " >> Noise level too high - adjusting")
            detector.raise_noise_floor()
        elif reason == 0x04:
            print_line(sourceID + " >> Disturber detected. Masking subsequent disturbers")
            detector.set_mask_disturber(True)
        elif reason == 0x08:
            #  we have a detection, let's start our period timer if it's not running already....
            if isPeriodTimerRunning() == False:
                startPeriodTimer()  # start our period
                first_alert = current_timestamp # remember when storm first started
            print_line(sourceID + " >> We sensed lightning! (%s)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'))
            if last_alert != datetime.min and (current_timestamp - last_alert).seconds < 3:
                print_line(" -- Last strike is too recent, incrementing counter since last alert.")
                strikes_since_last_alert += 1
                return
            if opt_testing == False:
                distance = detector.get_distance()
                energy = detector.get_energy()
            else:
                distance = synth_distance
                energy = synth_energy

            strikes_since_last_alert += 1

            distanceStr = str(distance) + "km"
            if distance == None:
                distanceStr = 'out-of-range'
            print_line(" -- Energy: " + str(energy) + " - Distance: " + distanceStr)

            # if we are past the end of this period then snap it and start accumulating all over
            if last_alert != datetime.min and (current_timestamp - last_alert).seconds > period_in_minutes * 60:
                print_line(sourceID + " >> Period ended, with detection in hand... reporting past first...")
                report_past_accumulator(prings_topic)
                strikes_since_last_alert = 1    # reset this since count just reported
                startPeriodTimer()  # RESET timer so it doesn't expire for another 'period_in_minutes'

            # ok, report our new detection to MQTT
            _thread.start_new_thread(send_status, (current_timestamp, energy, distance, strikes_since_last_alert))
            #  and let's accumulate this detection
            accumulate(current_timestamp, energy, distance, strikes_since_last_alert)
            report_current_accumulator(crings_topic)
            # setup for next...
            strikes_since_last_alert = 0
            # remember when most recent strike from this storm happened
            last_alert = current_timestamp
    else:
        # ----------------------------------
        # have period-end-timer interrupt!
        #   assume we are at the end of this period, snap it and start accumulating all over
        print_line(sourceID + " >> Period ended, waiting for next detection")
        report_past_accumulator(prings_topic)
        removeOldDetections()
        report_current_accumulator(crings_topic)
        # we snapped counters so reset count
        strikes_since_last_alert = 0

    # If no strike has been detected for the last hour, reset the strikes_since_last_alert (consider storm finished)
    if last_alert != datetime.min and (current_timestamp - last_alert).seconds > end_storm_after_minutes * 60:
        #_thread.start_new_thread(send_tweet, (
        #        "\o/ Thunderstorm over. No new flash detected for last 1/2h.",))
        print_line(sourceID + " >> Storm ended, waiting for next detection")
        report_past_accumulator(prings_topic)
        removeOldDetections()
        report_current_accumulator(crings_topic)
        resetStormTracking()    # kill awareness of any storm
        stopPeriodTimer()   #  kill our timer until our next detection
        #  reset our indicators
        strikes_since_last_alert = 0
        last_alert = datetime.min
        first_alert = datetime.min

resetAccumulatorToEmpty()
calculate_ring_widths()

# post setup data, once per run
settingsData = OrderedDict()
min_strikes = detector.get_min_strikes()
indoors = detector.get_indoors()
disp_lco = detector.get_display_lco()
noise_floor = detector.get_noise_floor()

if not disable_mqtt:
    _thread.start_new_thread(send_settings, (min_strikes, indoors, disp_lco, noise_floor))


# -----------------------------------------------------------------------------
#  Configure our interrupt handling
# -----------------------------------------------------------------------------

# if we are getting data from our live sensor then configure our interrupt pin
#  and attach our interrupt handler to it
if opt_testing == False and opt_calc_tuning_cap == False:

    # first clear our disturber... so it can reset itself...
    detector.set_mask_disturber(False)

    # now configure for run in main loop
    GPIO.add_event_detect(interrupt_pin, GPIO.RISING, callback=handle_interrupt)


# -----------------------------------------------------------------------------
#  Run our detection loop
# -----------------------------------------------------------------------------
if not disable_mqtt:
    print_line("Waiting for lightning - or at least something that looks like it", verbose=True)

if opt_testing == False and opt_calc_tuning_cap == False:
    # NOTE: we don't start our timer here... we wait until first detection!

    try:
        while True:
            # Read/clear the detector data every 10s in case we missed an interrupt (interrupts happening too fast ?)
            sleep(sleep_period)
            handle_interrupt(interrupt_pin)
    finally:
        # cleanup used pins... just because we like cleaning up after us
        stopPeriodTimer()   # don't leave our timers running!
        stopAliveTimer()
        GPIO.cleanup()
elif opt_calc_tuning_cap == True:
    # calculate our value and end the run
    print_line("Calculating Tuning Capacitor Value", verbose=True)
    detector.calculate_tuning_cap()
else:

    # we ARE testing, meaning we are loading detection info from our test file!
    # LINE IS: record-nbr, time-seconds, dist_km, energy
    test_file = open(test_filename, "r")
    lines = test_file.readlines()

    detection_count = 0
    for currLine in lines:
        if currLine.startswith("#"):
            continue
        detection_count += 1

    print_line('TESTing: - Running {} detections from "{}"'.format(detection_count, test_filename), verbose=True)

    curr_time_in_seconds = 0.0
    for currLine in lines:
        if currLine.startswith("#"):
            continue
        line_parts = currLine.split(',')
        print_line('- line_parts: [{}]'.format(line_parts), debug=True)
        dispatch_time_seconds = float(line_parts[1])
        synth_distance = float(line_parts[2])
        synth_energy = int(line_parts[3])
        wait_time = dispatch_time_seconds - curr_time_in_seconds
        print_line('- test entry: {}, {}, {}'.format(dispatch_time_seconds, synth_distance, synth_energy), debug=True)
        if opt_scale != 1 and wait_time != 0:
            wait_time /= opt_scale
        print_line('- waiting for {} seconds'.format(wait_time), debug=True)
        sleep(wait_time)
        handle_interrupt(TEST_INTERRUPT)
        curr_time_in_seconds = dispatch_time_seconds

    print_line("TESTing: Detections ended...  waiting to detect storm end", verbose=True)
    wait_time = 35 * 60 # storm is 30 minutes, let's add extra 5 min... then convert to seconds
    print_line('- waiting for {} seconds'.format(wait_time), debug=True)
    sleep(wait_time)

    stopPeriodTimer()   # don't leave our timers running!
    stopAliveTimer()
