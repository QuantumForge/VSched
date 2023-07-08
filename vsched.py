#!/usr/bin/env python

import argparse
from datetime import date, datetime, timedelta
import re
from string import Formatter
import subprocess
import sys
from zoneinfo import ZoneInfo

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
    def __init__(self, dt, fraction, label):
        self.dt = dt
        self.frac = fraction
        self.label = label
    def __lt__(self, other):
        return self.dt < other.dt
    def __str__(self):
        return self.label + ' ' + self.dt.strftime('%Y-%m-%d %H:%M') + \
            ' (' + str(self.frac) + ')'

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
        tokens = string.split(',')
        if len(tokens) != 8:
            raise RuntimeException(f'Bad line, wrong number of fields: {string}')
        self.sunset = event(datetime.fromisoformat(tokens[0]),
                            float(tokens[1]), 'sunset')
        self.sunrise = event(datetime.fromisoformat(tokens[2]),
                            float(tokens[3]), 'sunrise')
        self.moonset = event(datetime.fromisoformat(tokens[4]),
                            float(tokens[5]), 'moonset')
        self.moonrise = event(datetime.fromisoformat(tokens[6]),
                            float(tokens[7]), 'moonrise')

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
            if max(self.sunrise.frac, self.moonrise.frac) > max_rhv_phase:
                self.end_night = self.moonrise
            else:
                self.end_night = self.sunrise
        else:
            # only case left is setting moon
            if max(self.sunset.frac, self.moonset.frac) <= max_rhv_phase:
                self.start_night = self.sunset
            else:
                self.start_night = self.moonset
            self.end_night = self.sunrise

        self.night_duration = self.end_night.dt - self.start_night.dt
        return

    def print_night(self, print_moon_event):
        print('start_night:', self.start_night)
        if print_moon_event and self.moon_event is not None:
            print('moon_event:', self.moon_event)
        print('end_night:', self.end_night)
        print('night_duration:', self.night_duration)

    def find_dark(self):
        # if the moon is up all night, then this is bright run night
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
            # moon sets before sunrise, so it is dark
            if self.moonset.dt > self.moonrise.dt:
                self.start_dark = self.sunset
                self.end_dark   = self.sunrise
            # moon rises before sunrise, so it is not dark
            else:
                self.start_dark = None
                self.end_dark   = None
        # moon rises and sets after sunrise
        elif self.moonset.dt > self.sunrise.dt and \
                self.moonrise.dt > self.sunrise.dt:
            # moon rises before moon set meaning it was down between sunrise
            # and sunset, so it was dark
            if self.moonrise.dt < self.moonset.dt:
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
                if max(self.sunset.frac, self.sunrise.frac) < max_moon_phase:
                    self.moon_or_rhv = moon
                elif max(self.sunset.frac, self.sunrise.frac) < max_rhv_phase:
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
                if max(self.sunset.frac, self.sunrise.frac) < max_moon_phase:
                    self.moon_or_rhv = moon
                elif max(self.sunset.frac, self.sunrise.frac) < max_rhv_phase:
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
            if max(self.start_moon.frac, self.end_moon.frac) < max_moon_phase:
                self.moon_or_rhv = 'moon'
            elif max(self.start_moon.frac, self.end_moon.frac) < max_rhv_phase:
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
            if max(self.start_moon.frac, self.end_moon.frac) < max_moon_phase:
                self.moon_or_rhv = 'moon'
            elif max(self.start_moon.frac, self.end_moon.frac) < max_rhv_phase:
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

    def print_dr_sched(self, run_night, run_night_number, delim=','):
        start_night_ut = self.start_night.dt.astimezone(ZoneInfo('UTC'))
        print('', end=delim) # DR label
        # 'UTC Date'
        print(start_night_ut.strftime('%Y-%m-%d'), end=delim)
        # 'Start Date (MST)'
        print(self.start_night.dt.strftime('%Y-%m-%d'), end=delim)
        # 'DR #'
        print(f'DR{run_night:02d}-{run_night_number:02d}', end=delim)
        print('', end=delim)  # 'Day/Holidays'
        print('', end=delim)  # 'Day of week (MST)'
        print('', end=delim)  # 'holiday'
        print('', end=delim)  # 'Event Times'
        print(self.sunset.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        if self.moon_event is None:
            print('', end=delim)
            print('', end=delim)
        else:
            print(self.moon_event.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
            if self.moon_event.label == 'moonrise':
                print('Rise', end=delim)
            else:
                print('Set', end=delim)
        print(self.sunrise.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print('', end=delim)  # 'Run Times'
        print(self.start_night.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print(self.end_night.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print(self.start_dark.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        print(self.end_dark.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
        if self.moon_or_rhv is None:
            print('', end=delim)
            print('', end=delim)
        else:
            print(self.start_moon.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)
            print(self.end_moon.dt.strftime('%Y-%m-%d %H:%M:%S'), end=delim)

        #print('', end=',') # 'Night'

        #if self.dark_duration:
        #    print(strfdelta(self.dark_duration, '{H}:{M:02}:{S:02}'), end=',')
        #else:
        #    print('', end=',')

        #if self.moon_duration > timedelta(seconds=0) and \
        #        self.moon_or_rhv == 'moon':
        #    print(strfdelta(self.moon_duration, '{H}:{M:02}:{S:02}'),
        #          end=',')
        #    print('', end=',')
        #elif self.moon_duration > timedelta(seconds=0) and \
        #        self.moon_or_rhv == 'rhv':
        #    print('', end=',')
        #    print(strfdelta(self.moon_duration, '{H}:{M:02}:{S:02}'),
        #          end=',')
        #else:
        #    print('', end=',')
        #    print('', end=',')

        #print(strfdelta(self.night_duration, '{H}:{M:02}:{S:02}'), end=',')

        #print('', end=',') # 'Dark Run'
        #print('', end=',') # 'DR Dark'
        #print('', end=',') # 'DR Moonlight'
        #print('', end=',') # 'DR RHV'
        #print('', end=',') # 'DR Night'
        #print('', end=',') # 'DR Night #'

        #print('', end=',') # 'Season'
        #print('', end=',') # 'Season Dark'
        #print('', end=',') # 'Season Moonlight'
        #print('', end=',') # 'Season RHV'
        #print('', end=',') # 'Season Night'
        #print('', end=',') # 'Season Night #'

        print('', end=',') # 'Moon'
        if self.moon_or_rhv == 'moon' or self.moon_or_rhv == 'rhv' and \
                self.moon_duration > timedelta(seconds=0):
            print('{:.2f}'.format(max(self.start_moon.frac,
                                      self.end_moon.frac)*100), end=delim)
            print(self.moon_or_rhv, end=delim)
        else:
            print('', end=',') # 'Moon phase'


        print('')



    def print_sched2(self, dr, dr_night):
        max_rhv_phase  = 0.666
        max_moon_phase = 0.300
        minimum_interval = timedelta(hours=2)

        start_night = None
        moon_event  = None  # moon rise or set between sunset/sunrise
        end_night   = None

        start_dark = None
        end_dark = None

        start_moon = None
        end_moon = None 

        index = 0
        # step through ordered list until sunset. that's the first event
        # in which it is possible to observe
        while index < 4:
            if self.slist[index].label == 'sunset':
                break
            index += 1

        # earliest a night can start is at sunset. if moon is above horizon
        # we need to check illumination
        if self.slist[index].frac <= max_rhv_phase:
            start_night = self.slist[index]
            if self.slist[index].frac > 0.:
                start_moon = start_night
            else:
                start_dark = start_night
        index += 1


        # next event is a moon event or sunrise
        #if self.slist[index + 1].label == 'moonrise':
        #    moon_event = self.slist[index + 1]
        #    start_moon = moon_event

        #if self.slist[index + 1].label == 'moonset':
        #    moon_event = self.slist[index + 1]
        #    end_moon = moon_event

        # we only enter this loop because the moon was above the horizon
        # at sunset, but was too bright to begin observing. the only way
        # to start night now is to have moonset before sunrise.
        while start_night is None and index < 4:
            if self.slist[index].label == 'sunrise':
                end_night = self.slist[index]
                break

            if self.slist[index].label == 'moonset':
                start_night = self.slist[index]
                break
            index += 1

        while end_night is None and index < 4:
            if self.slist[index].label == 'sunrise' or \
                    (self.slist[index].label == 'moonrise' and 
                     self.slist[index].frac > max_rhv_phase):
                end_night = self.slist[index]
            index += 1

        if start_night is None or end_night is None:
            return 0

        if moon_event is None:
            start_dark = start_night
            end_dark   = end_night

        night_duration = end_night.dt - start_night.dt
        if dark_duration < minimum_interval:
            return 0

        print('duration:', duration.total_seconds()/3600.)
        start_night_ut = start_night.dt.astimezone(ZoneInfo('UTC'))
        print(start_night_ut.strftime('%Y-%m-%d'), end=',')
        print(start_night.dt.strftime('%Y-%m-%d'), end=',')
        print(f'DR{dr:02d}-{dr_night:02d}', end=',')
        print('', end=',')  # holiday
        print('', end=',')  # 'Event Times'
        print(self.sunset.dt.strftime('%Y-%m-%d %H:%M'), end=',')
        if moon_event is None:
            print('', end=',')
            print('', end=',')
        else:
            print(moon_event.dt.strftime('%Y-%m-%d %H:%M'), end=',')
            if moon_event.label == 'moonrise':
                print('Rise', end=',')
            else:
                print('Set', end=',')
        print(self.sunrise.dt.strftime('%Y-%m-%d %H:%M'), end=',')
        print('', end=',')  # 'Run Times'
        print(start_night.dt.strftime('%Y-%m-%d %H:%M'), end=',')
        print(end_night.dt.strftime('%Y-%m-%d %H:%M'), end=',')
        if start_dark is None or end_dark is None:
            print('', end=',')
            print('', end=',')
        else:
            print(start_dark.dt.strftime('%Y-%m-%d %H:%M'), end=',')
            print(end_dark.dt.strftime('%Y-%m-%d %H:%M'), end=',')
        if start_moon is None or end_moon is None:
            print('', end=',')
            print('', end=',')
        else:
            print(start_moon.dt.strftime('%Y-%m-%d %H:%M'), end=',')
            print(end_moon.dt.strftime('%Y-%m-%d %H:%M'), end=',')

        print('', end=',') # 'Night'
        print('', end=',') # 'Dark Time'
        print('', end=',') # 'Moonlight Time'
        print('', end=',') # 'RHV Time'
        print('', end=',') # 'Night Time'

        print('', end=',') # 'Dark Run'
        print('', end=',') # 'DR Dark'
        print('', end=',') # 'DR Moonlight'
        print('', end=',') # 'DR RHV'
        print('', end=',') # 'DR Night'
        print('', end=',') # 'DR Night #'

        print('', end=',') # 'Season'
        print('', end=',') # 'Season Dark'
        print('', end=',') # 'Season Moonlight'
        print('', end=',') # 'Season RHV'
        print('', end=',') # 'Season Night'
        print('', end=',') # 'Season Night #'

        print('', end=',') # 'Moon'
        print('', end=',') # 'Moon phase'




        print('')

        return 1

parser = argparse.ArgumentParser(description='Generate sun and moon rise/set times using same software as VERITAS loggen ephemeris.', epilog='Date format of start_date and stop_date is \'YYYY-MM-DD\'')
parser.add_argument('start_date', help='First night in range of nights to generate ephmeris. Use UT date; times are printed in local.')
parser.add_argument('stop_date', help='Last night in range of nights to generate ephmeris. Use UT date; times are printed in local')
parser.add_argument('-v', '--verbose', action='count', default=0,
                    help='Use mutliple times for more verbose output.')
parser.add_argument('--bright_run', action='store_true', 
                    help='Print bright run schedule.')
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
run_number = 0
run_night_number = 0
darkRun = False

scheduler = '/Users/whanlon/vephem'
dcounter = dtstart_date
while dcounter <= dtstop_date:
    #print('dcounter:', dcounter)
    callArgs = [scheduler]
    callArgs.append('-cl')
    callArgs.append(str(dcounter.year))
    callArgs.append(str(dcounter.month))
    callArgs.append(str(dcounter.day))
    proc = subprocess.run(callArgs, text=True, capture_output=True,
                          check=True)
    v = vephem(proc.stdout)
    if args.verbose:
        print(proc.stdout)
        print(v)

    if v.dark_duration >= minimum_interval:
        darkRun = True;
        if run_number == 0:
            run_number = 1
            run_night_number = 1
        else:
            run_night_number += 1

        v.print_dr_sched(run_number, run_night_number)
        #for e in v.slist:
        #    print(e)
        #print('')
        #v.print_night(False)
        #v.print_dark(False)
        #v.print_moon(False)
    else:
        if darkRun == True:
            run_number += 1
            run_night_number = 0
            darkRun = False

    #r = v.print_sched(dr, dr_night)
    #if r == 1:
    #    dr_night += 1
    #    darkRun = True
    #else:
    #    if darkRun == True:
    #        dr += 1
    #        dr_night = 1
    #        darkRun = False

    dcounter = dcounter + timedelta(days=1)
