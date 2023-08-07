#!/usr/bin/env python

import argparse
from datetime import date, datetime, timedelta, timezone
import re
from string import Formatter
import subprocess
import sys
from zoneinfo import ZoneInfo

# parameters that determine what is a dark run night, when to transition to
# RHV, moon, or SHV modes
max_rhv_phase  = 0.666
max_moon_phase = 0.300
minimum_interval = timedelta(hours=2)

def strfdelta(tdelta, fmt='{D:02}d {H:02}h {M:02}m {S:02}s', inputtype='timedelta'):
    """Convert a datetime.timedelta object or a regular number to a custom-
    formatted string, just like the stftime() method does for datetime.datetime
    objects.

    The fmt argument allows custom formatting to be specified.  Fields can
    include seconds, minutes, hours, days, and weeks.  Each field is optional.

    Some examples:
        '{D:02}d {H:02}h {M:02}m {S:02}s' --> '05d 08h 04m 02s' (default)
        '{W}w {D}d {H}:{M:02}:{S:02}'     --> '4w 5d 8:04:02'
        '{D:2}d {H:2}:{M:02}:{S:02}'      --> ' 5d  8:04:02'
        '{H}h {S}s'                       --> '72h 800s'

    The inputtype argument allows tdelta to be a regular number instead of the
    default, which is a datetime.timedelta object.  Valid inputtype strings:
        's', 'seconds',
        'm', 'minutes',
        'h', 'hours',
        'd', 'days',
        'w', 'weeks'
    """

    # Convert tdelta to integer seconds.
    if inputtype == 'timedelta':
        remainder = int(tdelta.total_seconds())
    elif inputtype in ['s', 'seconds']:
        remainder = int(tdelta)
    elif inputtype in ['m', 'minutes']:
        remainder = int(tdelta)*60
    elif inputtype in ['h', 'hours']:
        remainder = int(tdelta)*3600
    elif inputtype in ['d', 'days']:
        remainder = int(tdelta)*86400
    elif inputtype in ['w', 'weeks']:
        remainder = int(tdelta)*604800

    f = Formatter()
    desired_fields = [field_tuple[1] for field_tuple in f.parse(fmt)]
    possible_fields = ('W', 'D', 'H', 'M', 'S')
    constants = {'W': 604800, 'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    values = {}
    for field in possible_fields:
        if field in desired_fields and field in constants:
            values[field], remainder = divmod(remainder, constants[field])
    return f.format(fmt, **values)


class event:
    """class that characterizes sun/moon rise/set times. frac is fraction of
    moon illuminated at time dt, and alt is the moon altitude (degrees) at
    time dt. if frac < 0, the moon is below the horizon at time dt."""
    def __init__(self, dt, fraction, alt, label):
        self.dt = dt
        self.moon_frac = fraction
        self.moon_alt = alt
        self.label = label
    def __lt__(self, other):
        return self.dt < other.dt
    def __str__(self):
        return self.label + ' ' + self.dt.strftime('%Y-%m-%d %H:%M') + \
            ' (' + str(self.moon_frac) + ')'

class vsched_ap_action(argparse.Action):
    """Custom argparse action class to handle mutually exclusive options."""
    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
        if option_string == '--dark-run' or option_string == '-d':
            namespace.dark_run = True
            namespace.bright_run = False
        if option_string == '--bright-run' or option_string == '-b':
            namespace.bright_run = True
            namespace.dark_run = False

class vephem:
    def __init__(self, string):
        self.sunset = None
        self.sunrise = None
        self.moonset = None
        self.moonrise = None

        self.end_twilight = None
        self.moon_event   = None
        self.begin_twilight = None

        self.start_night = None
        self.end_night   = None
        self.night_duration = None

        # time when moon or rhv observing takes place
        self.start_moon  = None
        self.end_moon    = None
        self.moon_duration = None

        self.start_dark  = None
        self.end_dark    = None
        self.dark_duration = None

        # values are None, moon, or rhv
        self.moon_or_rhv = None

        # values are DR or BR
        self.night_type = None

        self.parse_string(string)
        # list of events sorted by time order
        self.slist = sorted([self.sunset, self.sunrise, self.moonset,
                             self.moonrise])
        #for e in self.slist:
        #    print(e)
        #print('')

        self.find_events()
        #self.print_events()

        self.find_night()
        self.find_dark()
        self.find_moon()
        #self.print_dark(False)


    def __str__(self):
        string = str(self.sunset) + '\n'
        string += str(self.sunrise) + '\n'
        string += str(self.moonset) + '\n'
        string += str(self.moonrise) + '\n'
        return string

    def parse_string(self, string):
        """Read string of output from the vnight ephemeris program. Expected
        output is time, moon illumination fraction, moon altitude for four
        event times: sun rise, sun set, moon rise, moon set."""
        tokens = string.split(',')
        if len(tokens) != 12:
            raise RuntimeException(f'Bad line, wrong number of fields: {string}')
        self.sunset = event(datetime.fromisoformat(tokens[0]),
                            float(tokens[1]), float(tokens[2]), 'sunset')
        self.sunrise = event(datetime.fromisoformat(tokens[3]),
                            float(tokens[4]), float(tokens[5]), 'sunrise')
        self.moonset = event(datetime.fromisoformat(tokens[6]),
                            float(tokens[7]), float(tokens[8]), 'moonset')
        self.moonrise = event(datetime.fromisoformat(tokens[9]),
                            float(tokens[10]), float(tokens[11]), 'moonrise')

    def find_events(self):
        """step through the time ordered list and find sunset, moon event (if
        there is one), and sunrise."""

        # find sunset first. no night can begin before sunset.
        i = 0
        while i < 4:
            if self.slist[i].label == 'sunset':
                break
            i += 1
        self.end_twilight = self.slist[i]

        # next event is moon rise, moon set, or sunrise
        i += 1
        if i >= 4:
            raise RuntimeException('index out of bounds')
        if self.slist[i].label == 'moonrise' or \
                self.slist[i].label == 'moonset':
            self.moon_event = self.slist[i]
            i += 1
            if i >= 4:
                raise RuntimeException('index out of bounds')
            self.begin_twilight = self.slist[i]

        # if begin_twilight is still not assigned then the event
        # following sunset was not a moon event. it must be
        # sunrise.
        self.begin_twilight = self.slist[i]

    def print_events(self):
        print('end_twilight: ', self.end_twilight)
        if self.moon_event is not None:
            print('moon_event: ', self.moon_event)
        print('begin_twilight: ', self.begin_twilight)


    def find_night(self):
        # if the moon is up all night, then this is bright run night
        if self.moonrise.dt < self.sunset.dt and \
                self.moonset.dt > self.sunrise.dt:
            self.start_night = self.sunset
            self.end_night   = self.sunrise
        # here the moon is down all night and the entire night is a dark night
        elif self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt > self.sunrise.dt:
            self.start_night = self.sunset
            self.end_night   = self.sunrise
        # unusual case where moonrise and moonset occur before/after sunset
        # this case needs a look. i think it only happens on nights near the
        # full moon.
        elif (self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt < self.sunset.dt) or \
                (self.moonset.dt > self.sunrise.dt and \
                self.moonrise.dt > self.sunrise.dt):
            self.start_night = self.sunset
            self.end_night   = self.sunrise
        # two cases left: moon rises or sets during the night
        elif self.moon_event.label == 'moonrise':
            self.start_night = self.sunset
            if max(self.sunrise.moon_frac, self.moonrise.moon_frac) > max_rhv_phase:
                self.end_night = self.moonrise
            else:
                self.end_night = self.sunrise
        else:
            # only case left is setting moon
            if max(self.sunset.moon_frac, self.moonset.moon_frac) <= max_rhv_phase:
                self.start_night = self.sunset
            else:
                self.start_night = self.moonset
            self.end_night = self.sunrise

        self.night_duration = self.end_night.dt - self.start_night.dt
        return

    def print_night(self, print_moon_event = True):
        print('start_night:', self.start_night)
        if print_moon_event and self.moon_event is not None:
            print('moon_event:', self.moon_event)
        print('end_night:', self.end_night)
        print('night_duration:', self.night_duration)

    def find_dark(self):
        # if the moon is up all night, then this is bright run night since
        # this only happens near full moon
        if self.moonrise.dt < self.sunset.dt and \
                self.moonset.dt > self.sunrise.dt:
            self.start_dark = None
            self.end_dark   = None
        # here the moon is down all night and the entire night is a dark night
        elif self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt > self.sunrise.dt:
            self.start_dark = self.sunset
            self.end_dark   = self.sunrise
        # moon rises and sets before sunset
        elif self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt < self.sunset.dt:
            # moon has set before sunrise, so it is dark the entire night
            if self.sunrise.moon_alt < 0:
                self.start_dark = self.sunset
                self.end_dark   = self.sunrise
            # otherwise the moon rose before sunrise, so it is not dark
            else:
                self.start_dark = None
                self.end_dark   = None
        # moon rises and sets after sunrise
        elif self.moonset.dt > self.sunrise.dt and \
                self.moonrise.dt > self.sunrise.dt:
            # moon was below horizon at sunset, so it is dark the entire night
            if self.sunrise.moon_alt < 0:
                self.start_dark = self.sunset
                self.end_dark   = self.sunrise
            # contrary case, moon was up between sunrise and sunset
            else:
                self.start_dark = None
                self.end_dark   = None
        # two cases left: moon rises or sets during the night
        elif self.moon_event.label == 'moonrise':
            self.start_dark = self.sunset
            self.end_dark = self.moonrise
        else:
            # only case left is setting moon
            self.start_dark = self.moonset
            self.end_dark = self.sunrise

        if self.end_dark is not None and self.start_dark is not None:
            self.dark_duration = self.end_dark.dt - self.start_dark.dt
        else:
            self.dark_duration = timedelta(seconds=0)

        return

    def print_dark(self, print_moon_event):
        print('start_dark:', self.start_dark)
        if print_moon_event and self.moon_event is not None:
            print('moon_event:', self.moon_event)
        print('end_dark:', self.end_dark)
        print('dark_duration:', self.dark_duration)

    def find_moon(self):
        """Find the times when moon is visible, calculate the duration,
        and determine if it is RHV or moonlight observing conditions."""
        # if the moon is up all night, then this is bright run night
        if self.moonrise.dt < self.sunset.dt and \
                self.moonset.dt > self.sunrise.dt:
            self.start_moon = self.sunset
            self.end_moon   = self.sunrise
            self.moon_or_rhv = None
        # here the moon is down all night and the entire night is a dark night
        elif self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt > self.sunrise.dt:
            self.start_moon = None
            self.end_moon   = None
            self.moon_or_rhv = None
        # both moonset and moonrise happen before sunset
        elif self.moonset.dt < self.sunset.dt and \
                self.moonrise.dt < self.sunset.dt:
            # moon rises before sunset, so it's up all night
            if self.moonrise.dt > self.moonset.dt:
                self.start_moon = self.sunset
                self.end_moon   = self.sunrise
                if max(self.sunset.moon_frac, self.sunrise.moon_frac) < max_moon_phase:
                    self.moon_or_rhv = moon
                elif max(self.sunset.moon_frac, self.sunrise.moon_frac) < max_rhv_phase:
                    self.moon_or_rhv = rhv
                else:
                    self.moon_or_rhv = None
            # moon sets before sunset, so no moon time
            else:
                self.start_moon = None
                self.end_moon   = None
                self.moon_or_rhv = None
        elif self.moonset.dt > self.sunrise.dt and \
                self.moonrise.dt > self.sunrise.dt:
            # moon was up all night
            if self.moonset.dt < self.moonrise.dt:
                self.start_moon = self.sunset
                self.end_moon = self.sunrise
                if max(self.sunset.moon_frac, self.sunrise.moon_frac) < max_moon_phase:
                    self.moon_or_rhv = moon
                elif max(self.sunset.moon_frac, self.sunrise.moon_frac) < max_rhv_phase:
                    self.moon_or_rhv = rhv
                else:
                    self.moon_or_rhv = None
            else:
                self.start_moon = None
                self.end_moon   = None
                self.moon_or_rhv = None
        # two cases left: moon rises or sets during the night
        elif self.moon_event.label == 'moonrise':
            self.start_moon = self.moonrise
            self.end_moon = self.sunrise
            if max(self.start_moon.moon_frac, self.end_moon.moon_frac) < max_moon_phase:
                self.moon_or_rhv = 'moon'
            elif max(self.start_moon.moon_frac, self.end_moon.moon_frac) < max_rhv_phase:
                self.moon_or_rhv = 'rhv'
            else:
                self.moon_or_rhv = None
        else:
            # only case left is setting moon
            self.start_moon = self.sunset
            self.end_moon = self.moonset
            # if moon rises during the night illumination increases while
            # it is above the horizon. make the determination of rhv/moon
            # based on its brightest.
            if max(self.start_moon.moon_frac, self.end_moon.moon_frac) < max_moon_phase:
                self.moon_or_rhv = 'moon'
            elif max(self.start_moon.moon_frac, self.end_moon.moon_frac) < max_rhv_phase:
                self.moon_or_rhv = 'rhv'
            else:
                self.moon_or_rhv = None

        if self.end_moon is not None and self.start_moon is not None:
            self.moon_duration = self.end_moon.dt - self.start_moon.dt
        else:
            self.moon_duration = timedelta(seconds=0)
        return

    def print_moon(self, print_moon_event):
        print('start_moon:', self.start_moon)
        if print_moon_event and self.moon_event is not None:
            print('moon_event:', self.moon_event)
        print('end_moon:', self.end_moon)
        print('moon_duration:', self.moon_duration)
        print('moon_or_rhv:', self.moon_or_rhv)

    def get_night_type(self):
        if self.dark_duration >= minimum_interval:
            self.night_type = 'DR'
        else:
            self.night_type = 'BR'

    def print_schedule(self, night_type, run_night, run_night_number,
                       delim=','):
        sunset_ut = self.sunset.dt.astimezone(ZoneInfo('UTC'))
        print('', end=delim) # DR label
        # 'UTC Date'
        print(sunset_ut.strftime('%Y-%m-%d'), end=delim)
        # 'Start Date (MST)'
        print(self.sunset.dt.strftime('%Y-%m-%d'), end=delim)
        # 'DR #'
        print(f'{night_type}{run_night:02d}-{run_night_number:02d}', end=delim)
        print('', end=delim)  # 'Day/Holidays'
        print('', end=delim)  # 'Day of week (MST)'
        print('', end=delim)  # 'holiday'
        print('', end=delim)  # 'Event Times'
        print(self.sunset.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        if self.moon_event is None:
            print('', end=delim)
            print('', end=delim)
            print('', end=delim)
        else:
            print(self.moon_event.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
            if self.moon_event.label == 'moonrise':
                print('Rise', end=delim)
            else:
                print('Set', end=delim)
            print('{:.2f}'.format(max(self.start_moon.moon_frac,
                                      self.end_moon.moon_frac)*100),
                  end=delim)
        # Twilight Begins (MST)
        print(self.sunrise.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print('', end=delim)  # 'Run Times'
        print(self.start_night.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print(self.end_night.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        if self.start_dark is not None and self.end_dark is not None:
            print(self.start_dark.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
            print(self.end_dark.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        else:
            print('', end=delim)
            print('', end=delim)
        if self.moon_or_rhv is None:
            print('', end=delim)
            print('', end=delim)
        else:
            print(self.start_moon.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
            print(self.end_moon.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)

        print('', end=',') # 'Moon'
        if self.moon_or_rhv == 'moon' or self.moon_or_rhv == 'rhv' and \
                self.moon_duration > timedelta(seconds=0):
            print('{:.2f}'.format(max(self.start_moon.moon_frac,
                                      self.end_moon.moon_frac)*100), end=delim)
            print(self.moon_or_rhv, end=delim)
        else:
            print('', end=',') # 'Moon phase'


        print('')


parser = argparse.ArgumentParser(description='Generate sun and moon rise/set times using same software as VERITAS loggen ephemeris.', epilog='Date format of start_date and stop_date is \'YYYY-MM-DD\' in UT time zone.')
parser.add_argument('start_date', help='First night in range of nights to generate ephmeris. Use UT date; times are printed in local.')
parser.add_argument('stop_date', help='Last night in range of nights to generate ephmeris. Use UT date; times are printed in local')
parser.add_argument('--night-program','-n', default='vnight',
                    help='Executable that outputs night event times.')
parser.add_argument('-v', '--verbose', action='count', default=0,
                    help='Use mutliple times for more verbose output.')
parser.add_argument('--bright-run','-b', action=vsched_ap_action, 
                    default=True, help='Print only bright run schedule.')
parser.add_argument('--dark-run','-d', action=vsched_ap_action, 
                    default=True, help='Print only dark run schedule.')
args = parser.parse_args()

rstart_date = re.fullmatch('(\d{4})-(\d{2})-(\d{2})', args.start_date, re.A)
rstop_date = re.fullmatch('(\d{4})-(\d{2})-(\d{2})', args.stop_date, re.A)

if rstart_date is None or rstop_date is None:
    print('Invalid date', file=sys.stderr)
    sys.exit(1)

dtstart_date = date(int(rstart_date.group(1)),
                             int(rstart_date.group(2)),
                             int(rstart_date.group(3)))
dtstop_date = date(int(rstop_date.group(1)),
                            int(rstop_date.group(2)),
                            int(rstop_date.group(3)))

# dark run and dark run night counters
dark_run_number = 0
dark_run_night_number = 0
bright_run_number = 0
bright_run_night_number = 0
# darkRun and brightRun state variables
darkRun = False
brightRun = False

scheduler = args.night_program
dcounter = dtstart_date
while dcounter <= dtstop_date:
    #print('dcounter:', dcounter)
    callArgs = [scheduler]
    # have vnight program output csv format, local times, and include time zone
    # information for each time it outputs
    callArgs.append('-clz')
    callArgs.append(str(dcounter.year))
    callArgs.append(str(dcounter.month))
    callArgs.append(str(dcounter.day))
    if args.verbose > 1:
        print('subprocess callArgs:', callArgs)
    proc = subprocess.run(callArgs, text=True, capture_output=True,
                          check=True)
    v = vephem(proc.stdout)
    if args.verbose:
        print('subprocess output:')
        print(proc.stdout)
        print('Events:')
        v.print_events()
        print('Night:')
        v.print_night()
        print('vephem obj')
        print(v)

    if args.dark_run == True:
        if v.dark_duration >= minimum_interval:
            darkRun = True;
            if dark_run_number == 0:
                dark_run_number = 1
                dark_run_night_number = 1
            else:
                dark_run_night_number += 1
            v.print_schedule('DR', dark_run_number, dark_run_night_number)
        else:
            if darkRun == True:
                dark_run_number += 1
                dark_run_night_number = 0
                darkRun = False

    if args.bright_run == True:
        if v.dark_duration < minimum_interval:
            brightRun = True;
            if bright_run_number == 0:
                bright_run_number = 1
                bright_run_night_number = 1
            else:
                bright_run_night_number += 1
            v.print_schedule('BR', bright_run_number, bright_run_night_number)
        else:
            if brightRun == True:
                bright_run_number += 1
                bright_run_night_number = 0
                brightRun = False
 
           
    # advance the date by one day.
    dcounter = dcounter + timedelta(days=1)
