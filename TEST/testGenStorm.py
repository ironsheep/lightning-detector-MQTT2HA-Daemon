#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import _thread
from datetime import datetime
import threading
import os
import sys
import re
import os.path
import argparse
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from configparser import ConfigParser
from colorama import init as colorama_init
from colorama import Fore, Back, Style
import random
from signal import signal, SIGPIPE, SIG_DFL
signal(SIGPIPE,SIG_DFL)

#
#  read config from storm_comfig.ini
#  the follow guidance to generate list of detections to output file (or stdout)
#  where each detection is
#     hh:mm distance energy

script_version = "1.0.0"
script_name = 'testGenStorm.py'

script_info = '{} v{}'.format(script_name, script_version)
project_info= '{}: Lightning Detections Generator'.format(script_info)
project_url = 'https://github.com/ironsheep/lightning-detector-MQTT2HA-Daemon'

if False:
    # will be caught by python 2.7 to be illegal syntax
    print('Sorry, this script requires a python3 runtime environment.', file=sys.stderr)

opt_debug = False
opt_verbose = False
opt_write_file = False
out_file = sys.stdout
output_line_count = 0

# Logging function
def print_line(text, error=False, warning=False, info=False, debug=False, write=False, console=True):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        elif info:
            if opt_verbose:
                print(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW  + '- ' + '{}'.format(text) + Style.RESET_ALL)
        elif debug:
            if opt_debug:
                print(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
        else:
            print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
    else:
        if write:
            print('{}'.format(text), file=out_file)
            #output_line_count += 1
# Argparse
default_output_filename = 'storm.dat'
default_config_filename = 'storm_config.ini'
parser = argparse.ArgumentParser(description=project_info, epilog='For further details see: ' + project_url)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
parser.add_argument("-d", "--debug", help="show debug output", action="store_true")
parser.add_argument("-w", "--write_output", help="write output to file", action="store_true")
parser.add_argument('--config_dir', help='directory where storm_config.ini is located', default=sys.path[0])
parser.add_argument("-i", '--config_file', help='name of config file to use instead of storm_config.ini', default=default_config_filename)
parser.add_argument("-o", '--output_file', help='name of file to be written', default=default_output_filename)
parse_args = parser.parse_args()


config_dir = parse_args.config_dir
config_filename = parse_args.config_file
output_filename = parse_args.output_file
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_write_file = parse_args.write_output

print_line(script_info, info=True)
print_line('Generating detections according to {} placing output in {}'.format(config_filename, output_filename), info=True)
if opt_verbose:
    print_line('Verbose enabled', info=True)
if opt_debug:
    print_line('Debug enabled', debug=True)


if opt_write_file and len(output_filename) > 0:
    out_file = open(output_filename, "wt")

# Load configuration file
config = ConfigParser(delimiters=('=', ), inline_comment_prefixes=('#'))
config.optionxform = str
try:
    with open(os.path.join(config_dir, config_filename)) as config_file:
        config.read_file(config_file)
except IOError:
    print_line('- No configuration file "{config_filename}"'.format(), error=True)
    sys.exit(1)

# Script storm settings



default_none = 0   # no default

min_percent = 1
max_percent = 100
#  percentage  1-100 - closet the storm will be (% of total time of storm)
storm_closest_from = config['Storm'].get('storm_closest_from', default_none)
storm_closest_to = config['Storm'].get('storm_closest_to', default_none)

# where will the stom be relative to overhead(0km) our location
default_min_distance = 0
default_max_distance = 63
storm_min_distance = config['Storm'].getint('storm_min_distance', default_min_distance)
storm_max_distance = config['Storm'].getint('storm_max_distance', default_max_distance)

# duration specs: minutes for this band in 'mm' -OR- 'hh:mm'
storm_early_duration = config['Storm'].get('storm_early_duration', default_none)
storm_middle_duration = config['Storm'].get('storm_middle_duration', default_none)
storm_late_duration = config['Storm'].get('storm_late_duration', default_none)

storm_early_strikes = config['Storm'].getint('storm_early_strikes', default_none)
storm_middle_strikes = config['Storm'].getint('storm_middle_strikes', default_none)
storm_late_strikes = config['Storm'].getint('storm_late_strikes', default_none)

# range specs  '{min_km}-{max_km}'
storm_early_energy = config['Storm'].get('storm_early_energy', default_none)
storm_middle_energy = config['Storm'].get('storm_middle_energy', default_none)
storm_late_energy = config['Storm'].get('storm_late_energy', default_none)

# parsers
def floatFromPercentSpec(percentSpec):
    desiredPercent = -1
    testPercent = float(percentSpec) / 100.0
    if testPercent >= 0 and testPercent <= 1:
        desiredPercent = testPercent
    return desiredPercent

def minutesFromHourSpec(hourSpec):
    desiredMinutes = -1
    partsAr = hourSpec.split(':')
    if len(partsAr) == 2:
        desiredMinutes = int(partsAr[0]) * 60
        desiredMinutes += int(partsAr[1])
    elif len(partsAr) == 1:
        desiredMinutes = int(partsAr[0])
    return desiredMinutes

def tupleFromRangeSpec(rangeSpec):
    desiredTuple = (-1)
    partsAr = rangeSpec.split('-')
    if len(partsAr) == 2:
        desiredTuple = ( int(partsAr[0]), int(partsAr[1]) )
    #else
        # parse error.... just let (-1) tuple be returned
    return desiredTuple

# Check configuration
#

have_enough_params = True

if (storm_min_distance < default_min_distance) or (storm_min_distance > default_max_distance):
    print_line('ERROR: Invalid "storm_min_distance" found in configuration file: "storm_config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(default_min_distance, default_max_distance), error=True)
    have_enough_params = False

if (storm_max_distance < default_min_distance) or (storm_max_distance > default_max_distance):
    print_line('ERROR: Invalid "storm_max_distance" found in configuration file: "storm_config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(default_min_distance, default_max_distance), error=True)
    have_enough_params = False

storm_early_duration_minutes = minutesFromHourSpec(storm_early_duration)
if (storm_early_duration_minutes < 0):
    print_line('ERROR: Invalid "storm_early_duration" found in configuration file: "storm_config.ini"! Invalid hh:mm [{}] Fix and try again... Aborting'.format(storm_early_duration), error=True)
    have_enough_params = False

storm_middle_duration_minutes = minutesFromHourSpec(storm_middle_duration)
if (storm_early_duration_minutes < 0):
    print_line('ERROR: Invalid "storm_middle_duration" found in configuration file: "storm_config.ini"! Invalid hh:mm [{}] Fix and try again... Aborting'.format(storm_middle_duration), error=True)
    have_enough_params = False

storm_late_duration_minutes = minutesFromHourSpec(storm_late_duration)
if (storm_early_duration_minutes < 0):
    print_line('ERROR: Invalid "storm_late_duration" found in configuration file: "storm_config.ini"! Invalid hh:mm [{}] Fix and try again... Aborting'.format(storm_late_duration), error=True)
    have_enough_params = False

storm_early_energy_range = tupleFromRangeSpec(storm_early_energy)
if storm_early_energy_range[0] == -1:
    print_line('ERROR: Invalid "storm_early_energy" found in configuration file: "storm_config.ini"! Invalid 999-999 [{}] Fix and try again... Aborting'.format(storm_early_energy), error=True)
    have_enough_params = False

storm_middle_energy_range = tupleFromRangeSpec(storm_middle_energy)
if storm_middle_energy_range[0] == -1:
    print_line('ERROR: Invalid "storm_middle_energy" found in configuration file: "storm_config.ini"! Invalid 999-999 [{}] Fix and try again... Aborting'.format(storm_middle_energy), error=True)
    have_enough_params = False

storm_late_energy_range = tupleFromRangeSpec(storm_late_energy)
if storm_late_energy_range[0] == -1:
    print_line('ERROR: Invalid "storm_late_energy" found in configuration file: "storm_config.ini"! Invalid 999-999 [{}] Fix and try again... Aborting'.format(storm_late_energy), error=True)
    have_enough_params = False

storm_closest_from_percent = floatFromPercentSpec(storm_closest_from)
if (storm_closest_from_percent == -1):
    print_line('ERROR: Invalid "storm_closest_from" found in configuration file: "storm_config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_percent, max_percent), error=True)
    have_enough_params = False

storm_closest_to_percent = floatFromPercentSpec(storm_closest_to)
if (storm_closest_to_percent == -1):
    print_line('ERROR: Invalid "storm_closest_to" found in configuration file: "storm_config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_percent, max_percent), error=True)
    have_enough_params = False

if have_enough_params == False:
    # things not good, we're outta here...
    sys.exit(1)


print_line('Configuration accepted', info=True)

total_minutes = storm_early_duration_minutes + storm_middle_duration_minutes + storm_late_duration_minutes

early_start_seconds = 0
middle_start_seconds = (60 * storm_early_duration_minutes) 
late_start_seconds = 60 * (storm_early_duration_minutes + storm_middle_duration_minutes) 

total_hours = int(total_minutes / 60)
total_rem_minutes = total_minutes - (total_hours * 60)

minutes_before_closest = total_minutes * storm_closest_from_percent
minutes_while_closest = (storm_closest_to_percent - storm_closest_from_percent) * total_minutes
minutes_after_closest = total_minutes * (1 - storm_closest_to_percent)
minutes_until_leave_closest = total_minutes - minutes_after_closest

print_line('- minutes_before_closest {}'.format(round(minutes_before_closest,1)))
print_line('- total_minutes {} [{}:{}]'.format(total_minutes, total_hours, total_rem_minutes))
print_line('- minutes_while_closest {}'.format(round(minutes_while_closest,1)))
print_line('- minutes_until_leave_closest {}'.format(round(minutes_until_leave_closest,1)))
print_line('- minutes_after_closest {}'.format(round(minutes_after_closest,1)))

def timeHMSfromSeconds(timeSeconds):
    desiredhours = int(timeSeconds / 3600)
    seconds_remaining = timeSeconds - (desiredhours * 3600)
    desiredMinutes = int(seconds_remaining / 60)
    seconds_remaining -= (desiredMinutes * 60)
    return ( desiredhours, desiredMinutes, round(seconds_remaining))

def stringTimeSecondsInHMS(curr_time_in_seconds):
    time_hms = timeHMSfromSeconds(curr_time_in_seconds)
    desired_interp = '{:02d}:{:02d}:{:02d}'.format(time_hms[0], time_hms[1], time_hms[2])
    return desired_interp


close_start_hms = stringTimeSecondsInHMS((minutes_before_closest * 60) + 1)
close_end_hms = stringTimeSecondsInHMS(((minutes_before_closest + minutes_while_closest) * 60) - 1)

close_minutes = round(minutes_before_closest + minutes_while_closest)
close_end_hours = int(close_minutes / 60)
close_end_rem_minutes = close_minutes - (close_end_hours * 60)

print_line('- close {} - {}'.format(close_start_hms, close_end_hms))

early_hours = int(storm_early_duration_minutes / 60)
early_rem_minutes = int(storm_early_duration_minutes - (early_hours * 60))
early_rem_seconds = storm_early_duration_minutes - ((early_hours * 3600) + (early_rem_minutes * 60))



early_start_hms = stringTimeSecondsInHMS(0)
early_end_hms = stringTimeSecondsInHMS(0 + (60 * (storm_early_duration_minutes - 1/60)))

middle_start_hms = stringTimeSecondsInHMS(middle_start_seconds)
middle_end_hms = stringTimeSecondsInHMS(middle_start_seconds + (60 * (storm_middle_duration_minutes - 1/60)))

late_start_hms = stringTimeSecondsInHMS(late_start_seconds)
late_end_hms = stringTimeSecondsInHMS(late_start_seconds + (60 * (storm_late_duration_minutes - 1/60)))


early_middle_minutes = storm_early_duration_minutes + storm_middle_duration_minutes
minutes_so_far = early_middle_minutes
middle_hours = int(minutes_so_far / 60)
middle_rem_minutes = minutes_so_far - (middle_hours * 60)

minutes_so_far = total_minutes
late_hours = int(minutes_so_far / 60)
late_rem_minutes = minutes_so_far - (late_hours * 60)

print_line('- early {} - {}'.format(early_start_hms, early_end_hms))
print_line('- mid   {} - {}'.format(middle_start_hms, middle_end_hms))
print_line('- late  {} - {}'.format(late_start_hms, late_end_hms))

close_early = False
close_middle = False
close_late = False
if minutes_before_closest > storm_early_duration_minutes:
    if minutes_before_closest > early_middle_minutes:
        print_line('Close during late!', debug=True)
        close_late = True
    else:
        print_line('Close during middle!', debug=True)
        close_middle = True
else:
    print_line('Close during early!', debug=True)
    close_early = True

if close_minutes < storm_early_duration_minutes:
    print_line('Leave Close during early!', debug=True)
else:
    if close_minutes > early_middle_minutes:
        print_line('Leave Close during late!', debug=True)
        if close_early:
            close_middle = True
        close_late = True
    else:
        print_line('Leave Close during middle!', debug=True)
        close_middle = True

if close_early:
    print_line('Close EARLY', info=True)
if close_middle:
    print_line('Close MIDDLE', info=True)
if close_late:
    print_line('Close LATE', info=True)
# distance calcs
#  - storm moves from farthest to closest from 00:00 to 'minutes_before_closest'
#  - stays close until 'close_minutes'
#  - moves from closest to farthest from 'minutes_until_leave_closest' to 'total_minutes'
# rate of travel 
#  - distance: max km - min_distance
#  - entry-rate km/min =  distance / 'minutes_before_closest'
#  - rate of 0 until 'minutes_until_leave_closest'
#  - exit-rate km/min = distance / ('total_minutes' - 'minutes_until_leave_closest')
distance_traveled = storm_max_distance - storm_min_distance
entry_rate = round(0 - (distance_traveled / minutes_before_closest),1)
exit_rate = round(distance_traveled / (total_minutes - minutes_until_leave_closest), 1)
print_line('- distance_traveled {} km (twice: arrive then leave)'.format(distance_traveled))
print_line('- storm arrives @ {} km/min'.format(round(entry_rate,1)))
print_line('- storm departs @ {} km/min'.format(round(exit_rate,1)))

# generate detections for early then middle then late
#  watch for overlap with CLOSE/OVERHEAD
#
#  generate 'storm_early_strikes' of 'storm_early_energy' within 'storm_early_duration'
#  generate 'storm_middle_strikes' of 'storm_middle_energy' within 'storm_middle_duration'
#  generate 'storm_late_strikes' of 'storm_late_energy' within 'storm_late_duration'

#  table   LATE - MIDDLE - EARLY
#           0       0        0  >> can't happen <<
#           0       0        1  contained
#           0       1        0  contained
#           0       1        1  SPANs
#           1       0        0  contained
#           1       0        1  >> can't happen <<
#           1       1        0  SPANs
#           1       1        1  SPANs
#
normal_early = False
normal_middle = False
normal_late = False
split_early = False
split_middle = False
split_late = False
close_only_middle = False
middle_is_exit_rate = False

if close_early == False and close_middle == False and close_late == False:
    # ERROR must have a close somewhere
    print_line('ERROR[CODE]??: Missing CLOSE spec... Aborting', error=True)
    sys.exit(1)

elif close_early == True and close_middle == False and close_late == True:
    # ERROR close is over early and late but not middle, 
    #   can't happen
    print_line('ERROR[CODE]??: close is NOT over middle... Aborting', error=True)
    sys.exit(1)

elif close_early == True and close_middle == False  and close_late == False:
    # close contained in EARLY section
    #  2 or 3 early sections
    split_early = True
    #  1 middle section
    normal_middle = True
    middle_is_exit_rate = True
    #  1 late section
    normal_late = True   
       
elif close_early == False and close_middle == True and close_late == False:
    # close contained in MIDDLE section
    #  1 early section
    normal_early = True
    #  2 or 3 middle sections
    split_middle = True
    #  1 late section
    normal_late = True 

elif close_early == True and close_middle == True and close_late == False:
    # close SPANs EARLY and MIDDLE sections
    # 1 or 2 early sections
    split_early = True
    # 1 or 2 middle sections
    split_middle = True
    # 1 late section
    normal_late = True 

elif close_early == False  and close_middle == False  and close_late == True:
    # close contained in LATE section
    #  1 early section
    normal_early = True 
    #  1 middle section
    normal_middle = True 
    middle_is_exit_rate = False
    #  2 or 3 late sections
    split_late = True

elif close_early == False  and close_middle == True and close_late == True:
    # close SPANs LATE and MIDDLE sections
    #  1 early section
    normal_early = True 
    #  1 or 2 middle sections
    split_middle = True
    #  1 or 2 late sections
    split_late = True
else:
    # close SPANs LATE, MIDDLE and EARLY sections
    #  1 or 2 early sections
    split_early = True
    #  1 middle sections (all close)
    close_only_middle = True
    #  1 or 2 late sections
    split_late = True

#  emit our generator control tuples
#   where each tuple is (distance rate change +/-, # detections, energy range, duration )
generatorSets = []
# -----------------------------------------------------
#   EARLY
minutes_close_so_far = 0
curr_distance = storm_max_distance
if normal_early == True:
    generatorSets.append( ( 'earlyOnly', entry_rate, storm_early_strikes, storm_early_energy_range, storm_early_duration_minutes ) )

elif split_early == True:
    #  hmm need to spread strikes across all parts
    rem_minutes_early = storm_early_duration_minutes
    rem_early_strikes = storm_early_strikes
    
    if minutes_before_closest > 0:
        # have early before CLOSE
        percent_early = minutes_before_closest / storm_early_duration_minutes
        before_strikes = round(storm_early_strikes * percent_early)
        generatorSets.append( ( 'earlyBefore', entry_rate, before_strikes, storm_early_energy_range, round(minutes_before_closest,3) ) )
        rem_minutes_early -= minutes_before_closest
        rem_early_strikes -= before_strikes
        
    if minutes_until_leave_closest < storm_early_duration_minutes:
        # generate CLOSE part
        percent_close = minutes_while_closest / rem_minutes_early
        close_strikes = round(rem_early_strikes * percent_close)
        generatorSets.append( ( 'earlyClose', 0, close_strikes, storm_early_energy_range, round(minutes_while_closest,3) ) )
        rem_minutes_early -= minutes_while_closest
        rem_early_strikes -= close_strikes
        minutes_close_so_far = minutes_while_closest
        # generate trailing EARLY-NOT_CLOSE part
        generatorSets.append( ( 'earlyAfter', exit_rate, rem_early_strikes, storm_early_energy_range, round(rem_minutes_early,3) ) )
    else:
        # end with CLOSE part
        generatorSets.append( ( 'earlyClose', 0, storm_early_strikes, storm_early_energy_range, round(rem_minutes_early,3) ) )
        minutes_close_so_far = rem_minutes_early

# -----------------------------------------------------
#   MIDDLE
if normal_middle == True:
    desired_rate = entry_rate
    if middle_is_exit_rate:
        desired_rate = exit_rate
    generatorSets.append( ( 'middleOnly', desired_rate, storm_middle_strikes, storm_middle_energy_range, round(storm_middle_duration_minutes,3) ) )

elif close_only_middle == True:
    generatorSets.append( ( 'middleCloseOnly', 0, storm_middle_strikes, storm_middle_energy_range, round(storm_middle_duration_minutes,3) ) )
    minutes_close_so_far += storm_middle_duration_minutes

elif split_middle == True:
    #  hmm need to spread strikes across all parts
    rem_minutes_middle = storm_middle_duration_minutes
    rem_middle_strikes = storm_middle_strikes
    start_of_middle = storm_early_duration_minutes
    rem_close_minutes = minutes_while_closest - minutes_close_so_far
    
    if minutes_before_closest > start_of_middle:
        # have middle before CLOSE
        minutes_before = minutes_before_closest - start_of_middle
        percent_middle = minutes_before_closest / storm_middle_duration_minutes
        before_strikes = int(storm_middle_strikes * percent_middle)
        generatorSets.append( ( 'middleBefore', entry_rate, before_strikes, storm_middle_energy_range, round(minutes_before,3) ) )
        rem_minutes_middle -= minutes_before
        rem_middle_strikes -= before_strikes
        
    if rem_close_minutes < rem_minutes_middle:
        # generate CLOSE part
        percent_close = rem_close_minutes / rem_minutes_middle
        close_strikes = int(rem_middle_strikes * percent_close)
        generatorSets.append( ( 'middleClose', 0, close_strikes, storm_middle_energy_range, round(rem_close_minutes,3) ) )
        rem_minutes_middle -= rem_close_minutes
        rem_middle_strikes -= close_strikes
        minutes_close_so_far += rem_close_minutes
        # generate trailing MIDDLE-NOT-CLOSE part
        generatorSets.append( ( 'middleAfter', exit_rate, rem_middle_strikes, storm_middle_energy_range, round(rem_minutes_middle,3) ) )
    else:
        # end with CLOSE part
        generatorSets.append( ( 'middleEndClose', 0, rem_middle_strikes, storm_middle_energy_range, round(rem_minutes_middle,3) ) )
        minutes_close_so_far += rem_minutes_middle

# -----------------------------------------------------
#   LATE
if normal_late == True:
        generatorSets.append( ( 'lateOnly', exit_rate, storm_late_strikes, storm_late_energy_range, round(storm_late_duration_minutes,3) ) )

elif split_late == True:
    #  hmm need to spread strikes across all parts
    rem_minutes_late = storm_late_duration_minutes
    rem_late_strikes = storm_late_strikes
    start_of_late = storm_early_duration_minutes + storm_middle_duration_minutes
    rem_close_minutes = minutes_while_closest - minutes_close_so_far

    if minutes_before_closest > start_of_late:
        # have late before CLOSE
        minutes_before = minutes_before_closest - start_of_late
        percent_late = minutes_before_closest / storm_late_duration_minutes
        before_strikes = int(storm_late_strikes * percent_late)
        generatorSets.append( ( 'lateBefore', entry_rate, before_strikes, storm_late_energy_range, round(minutes_before,3) ) )
        rem_minutes_late -= minutes_before
        rem_late_strikes -= before_strikes
        
    if rem_close_minutes < rem_minutes_late:
        # generate CLOSE
        percent_close = rem_close_minutes / rem_minutes_late
        close_strikes = int(rem_late_strikes * percent_close)
        generatorSets.append( ( 'lateClose', 0, close_strikes, storm_late_energy_range, round(rem_close_minutes,3) ) )
        rem_minutes_late -= rem_close_minutes
        rem_late_strikes -= close_strikes
        minutes_close_so_far += rem_close_minutes
        # generate trailing LATE-NOT-CLOSE part
        generatorSets.append( ( 'lateAfter', exit_rate, rem_late_strikes, storm_late_energy_range, round(rem_minutes_late,3) ) )
    else:
        # end with CLOSE part
        generatorSets.append( ( 'lateEndClose', 0, rem_late_strikes, storm_late_energy_range, round(rem_minutes_late,3) ) )
        minutes_close_so_far += rem_minutes_late

set_time_in_seconds = 0
for currSet in generatorSets:
    start_time_hms = stringTimeSecondsInHMS(set_time_in_seconds)
    set_time_in_seconds += (currSet[4] * 60)
    end_time_hms = stringTimeSecondsInHMS(set_time_in_seconds - 1)
    tupleString = '{}'.format(currSet)
    print_line('generator set --- {:>45} --- {} - {}'.format(tupleString, start_time_hms, end_time_hms), debug=True)


# -----------------------------------------------------
#   Let's generate us some lightning!
#
curr_distance = storm_max_distance
curr_time_in_seconds = 0

def byTime(e):
    return e[0]


if opt_write_file:
    print_line('Writing file: {}'.format(output_filename), info=True)
    output_line_count = 0

print_line('{}'.format('# record-nbr, time-seconds, dist_km, energy'), console=False, write=True)
line_number = 1
for currSet in generatorSets:
    curr_time_hmsstr = stringTimeSecondsInHMS(curr_time_in_seconds)
    print_line('start {} from {} km'.format(curr_time_hmsstr, curr_distance))
    print_line('# start {} from {} km'.format(curr_time_hmsstr, curr_distance), console=False, write=True)
    # generate lightning for set...
    #   ( name, travel-rate, count, energy(min-max), duration_in_minutes )
    print_line('- set {}'.format(currSet[0]), debug=True)
    print_line(' -- rate={}'.format(currSet[1]), debug=True)
    print_line(' -- ct={}'.format(currSet[2]), debug=True)
    print_line(' -- energy={}'.format(currSet[3]), debug=True)
    print_line(' -- minutes={}'.format(currSet[4]), debug=True)

    #     hh:mm distance energy
    detections = []
    scale = 100 # handle 9.99 format float
    for i in range(currSet[2]):
        when = random.randrange(0, currSet[4] * scale)
        if when > 0:
            when /= scale
        time_in_seconds = round(((when * 60) + curr_time_in_seconds),1)
        energy_range = currSet[3]
        #print_line(' -- energy_range={}'.format(energy_range), debug=True)
        energy = random.randrange(energy_range[0], energy_range[1])
        distance_km = round((when * currSet[1]) + curr_distance)
        detections.append( (time_in_seconds, distance_km, energy) )

    
    for detection in sorted(detections, key=byTime):
        curr_time_hmsstr = stringTimeSecondsInHMS(detection[0])
        print_line('  -- detection={} {}'.format(curr_time_hmsstr, detection), debug=True)
        print_line('{}, {}, {}, {}'.format(line_number, detection[0], detection[1], detection[2]), console=False, write=True)
        line_number += 1
 
    curr_time_in_seconds += currSet[4] * 60
    curr_distance = round((currSet[4] * currSet[1]) + curr_distance,1)

if opt_write_file:
    print_line('Write {} lines to file: {}'.format(output_line_count, output_filename), info=True)