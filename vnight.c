#include <getopt.h>
#include <libgen.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "slalib.h"
#include "libnova/julian_day.h"
#include "libnova/lunar.h"
#include "libnova/solar.h"
#include "libnova/transform.h"
#include "libnova/utility.h"

/* gcc -o vephem vephem.c -I/Users/whanlon/starlink/include/star -I/Users/whanlon/local/include -L/Users/whanlon/starlink/lib -L/Users/whanlon/local/lib/ -lc -lsla -lnova */

/* latitude and longitude used in software/offline/dqm/cgi-bin/skysurvey/
   db_scheduler/include/VScheduler.h */
const double veritas_latitude   = 31.675;
const double veritas_longitude  = -110.952;

/* angles of sun relative to horizon used to define VERITAS twilight. defined
   in software/offline/dqm/cgi-bin/skysurvey/db_scheduler/src/printRiseSet.cpp
*/
const double horizon_angle_begin = -16.5;
const double horizon_angle_end = -15.;

char *pname;

void usage()
{
    printf("usage: %s YEAR MONTH DAY\n", pname);
    printf("  -c, --csv   Dump output in CSV format for spreadsheet.\n");
    printf("  -h, --help  Print this message and exit.\n");
    printf("  -l, --local Output times in MST timezone.\n");
    printf("\nYear must be four digits. Date is UT date.\n\n");
    printf("Output format is comma separated list of:\n");
    printf("Sun Set, %% Moon @ Sun Set, Sun Rise, Moon Rise, %% Moon at rise, "
            "Moon Set\n");
    printf("Times are UT unless -l switch is used. \"%% Moon @ sun set\" "
            "is empty if the moon\nis not above the horizon at sun set.\n");

    return;
}

struct ephem_data
{
    int ut_year;
    int ut_month;
    int ut_day;
    int ut_hour;
    int ut_minute;
    double ut_second;
    int local_year;
    int local_month;
    int local_day;
    int local_hour;
    int local_minute;
    double local_second;
    /* if used for sun, fraction is moon fraction at the time stored here.
       if the moon is not above the horizon, then fraction is < 0 */
    double moon_fraction; /* fraction of the moon that is illuminated [0 - 1] */
    double jd; /* julian date of the event computed from libnova */
    char label[16];
};

int get_moon_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set);
int get_sun_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set);
void print_ephem_data(const struct ephem_data *data, int ut_time,
        int csv, int verbose);
void print_csv(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time);
void print_ordered(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time);
int ephem_compar(const void *a, const void *b);

int main(int argc, char **argv)
{
    char argv0[256];
    strncpy(argv0, argv[0], sizeof(argv0) - 1);
    argv0[sizeof(argv0) - 1] = '\0';
    pname = basename(argv0);

    int opt_csv = 0;
    int opt_help = 0;
    int opt_ut = 1;
    static struct option longopts[] =
    {
        {"csv",     no_argument,     NULL,   'c'},
        {"help",    no_argument,     NULL,   'h'},
        {"local",   no_argument,     NULL,   'l'},
        {NULL,      0,               NULL,   0}
    };

    int c;
    while((c = getopt_long(argc, argv, "chl", longopts, NULL)) != -1)
    {
        switch (c)
        {
            case 'c':
               opt_csv = 1;
               break; 
            case 'l':
               opt_ut = 0;
               break;
            case 'h':
            case '?':
            default:
               opt_help = 1;
               break;
        }
    }

    if (opt_help)
    {
        usage();
        exit(EXIT_SUCCESS);
    }

    if (argc - optind != 3)
    {
        usage();
        exit(EXIT_FAILURE);
    }

    if (strlen(argv[optind]) != 4)
    {
        fprintf(stderr, "%s: Invalid year.\n", pname);
        exit(EXIT_FAILURE);
    }

    unsigned long ut_year, ut_month, ut_day;
    ut_year  = strtoul(argv[optind], NULL, 10);
    ut_month = strtoul(argv[optind + 1], NULL, 10);
    ut_day   = strtoul(argv[optind + 2], NULL, 10);
    
    if (ut_month > 12)
    {
        fprintf(stderr, "%s: Invalid month.\n", pname);
        exit(EXIT_FAILURE);
    }

    if (ut_day > 31)
    {
        fprintf(stderr, "%s: Invalid day.\n", pname);
        exit(EXIT_FAILURE);
    }

    /* get the moon rise and set times */
    struct ephem_data moon_rise;
    struct ephem_data moon_set;
    get_moon_rise_set(ut_year, ut_month, ut_day, &moon_rise, &moon_set);
    /* print_ephem_data(&moon_rise, opt_ut, 0, 1); */
    /* print_ephem_data(&moon_set, opt_ut, 0, 1); */

    /* get the sun rise and set times */
    struct ephem_data sun_rise;
    struct ephem_data sun_set;
    get_sun_rise_set(ut_year, ut_month, ut_day, &sun_rise, &sun_set);
    /* print_ephem_data(&sun_rise, opt_ut, 0, 1); */
    /* print_ephem_data(&sun_set, opt_ut, 0, 1); */

    if (opt_csv)
        print_csv(&sun_set, &sun_rise, &moon_set, &moon_rise, opt_ut);
    else
        print_ordered(&sun_set, &sun_rise, &moon_set, &moon_rise, opt_ut);

    exit(EXIT_SUCCESS);
}


int get_moon_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set)
{
    struct ln_lnlat_posn observer;
    observer.lat = veritas_latitude;
    observer.lng = veritas_longitude;

    int status;
    double mjd;

    slaCaldj((int)year, (int)month, (int)day, &mjd, &status);
    if (status != 0)
    {
        fprintf(stderr, "%s: slaCaldj failed.\n", pname);
        exit(EXIT_FAILURE);
    }

    double jd = mjd + 2400000;

    struct ln_rst_time lunar_rst;
    status = ln_get_lunar_rst(jd, &observer, &lunar_rst);
    /* if status = 1, then moon is circumpolar and remains above or below
     * the horizon for the entire day */
    if (status == 1)
    {
        fprintf(stderr, "%s: Warning moon is circumpolar\n", pname);
        return 1;
    }
        
    struct ln_date lunar_ut_rise;
    ln_get_date(lunar_rst.rise, &lunar_ut_rise);
    rise->ut_year = lunar_ut_rise.years;
    rise->ut_month = lunar_ut_rise.months;
    rise->ut_day = lunar_ut_rise.days;
    rise->ut_hour = lunar_ut_rise.hours;
    rise->ut_minute = lunar_ut_rise.minutes;
    rise->ut_second = lunar_ut_rise.seconds;
    struct ln_zonedate lunar_local_rise;
    ln_get_local_date(lunar_rst.rise, &lunar_local_rise);
    rise->local_year = lunar_local_rise.years;
    rise->local_month = lunar_local_rise.months;
    rise->local_day = lunar_local_rise.days;
    rise->local_hour = lunar_local_rise.hours;
    rise->local_minute = lunar_local_rise.minutes;
    rise->local_second = lunar_local_rise.seconds;

    rise->moon_fraction = ln_get_lunar_disk(lunar_rst.rise);
    rise->jd = lunar_rst.rise;
    strcpy(rise->label, "Moon Rise");

    struct ln_date lunar_ut_set;
    ln_get_date(lunar_rst.set,  &lunar_ut_set);
    set->ut_year = lunar_ut_set.years;
    set->ut_month = lunar_ut_set.months;
    set->ut_day = lunar_ut_set.days;
    set->ut_hour = lunar_ut_set.hours;
    set->ut_minute = lunar_ut_set.minutes;
    set->ut_second = lunar_ut_set.seconds;
    struct ln_zonedate lunar_local_set;
    ln_get_local_date(lunar_rst.set, &lunar_local_set);
    set->local_year = lunar_local_set.years;
    set->local_month = lunar_local_set.months;
    set->local_day = lunar_local_set.days;
    set->local_hour = lunar_local_set.hours;
    set->local_minute = lunar_local_set.minutes;
    set->local_second = lunar_local_set.seconds;

    set->moon_fraction = ln_get_lunar_disk(lunar_rst.set);
    set->jd = lunar_rst.set;
    strcpy(set->label, "Moon Set");

    return 0;
}

/* calculates sun rise and set times for UT date specified by year, month, and
   day. moon_rise data is given to provide moon fraction at sun rise and
   set times. if the moon is not above the horizon, then fraction is set to
   -1. */
int get_sun_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set)
{
    struct ln_lnlat_posn observer;
    observer.lat = veritas_latitude;
    observer.lng = veritas_longitude;

    int status;
    double mjd;

    slaCaldj((int)year, (int)month, (int)day, &mjd, &status);
    if (status != 0)
    {
        fprintf(stderr, "%s: slaCaldj failed.\n", pname);
        exit(EXIT_FAILURE);
    }

    double jd = mjd + 2400000;

    /* multiple calls are required for rise and set times because position
       below the horizon is different for VERITAS twilight at the beginning
       and end of observing nights */
    struct ln_rst_time solar_rst;
    /* compute sun set first */
    status = ln_get_solar_rst_horizon(jd, &observer, horizon_angle_begin,
            &solar_rst);
    /* if status = 0, success
       if status = 1 (-1), then sun is circumpolar and remains above (below)
       the horizon for the entire day */
    if (status != 0)
    {
        fprintf(stderr, "%s: Warning sun is circumpolar\n", pname);
        return 1;
    }
    else
    {
        struct ln_date solar_ut_set;
        ln_get_date(solar_rst.set, &solar_ut_set);
        set->ut_year = solar_ut_set.years;
        set->ut_month = solar_ut_set.months;
        set->ut_day = solar_ut_set.days;
        set->ut_hour = solar_ut_set.hours;
        set->ut_minute = solar_ut_set.minutes;
        set->ut_second = solar_ut_set.seconds;
        struct ln_zonedate solar_local_set;
        ln_get_local_date(solar_rst.set, &solar_local_set);
        set->local_year = solar_local_set.years;
        set->local_month = solar_local_set.months;
        set->local_day = solar_local_set.days;
        set->local_hour = solar_local_set.hours;
        set->local_minute = solar_local_set.minutes;
        set->local_second = solar_local_set.seconds;

        /* is the moon above the horizon when the sun sets? */
        struct ln_equ_posn moon_equ_at_sunset;
        ln_get_lunar_equ_coords(solar_rst.set, &moon_equ_at_sunset);
        struct ln_hrz_posn moon_hrz_at_sunset;
        ln_get_hrz_from_equ(&moon_equ_at_sunset, &observer, solar_rst.set,
                &moon_hrz_at_sunset);

        if (moon_hrz_at_sunset.alt > 0.)
            set->moon_fraction = ln_get_lunar_disk(solar_rst.set);
        else
            set->moon_fraction = -1.;

        set->jd = solar_rst.set;
        strcpy(set->label, "Sun Set");
    }

    /* now compute sun rise */
    status = ln_get_solar_rst_horizon(jd, &observer, horizon_angle_end,
            &solar_rst);
    /* if status = 0, success
       if status = 1 (-1), then sun is circumpolar and remains above (below)
       the horizon for the entire day */
    if (status != 0)
    {
        fprintf(stderr, "%s: Warning sun is circumpolar\n", pname);
        return 1;
    }
    else
    {
        struct ln_date solar_ut_rise;
        ln_get_date(solar_rst.rise, &solar_ut_rise);
        rise->ut_year = solar_ut_rise.years;
        rise->ut_month = solar_ut_rise.months;
        rise->ut_day = solar_ut_rise.days;
        rise->ut_hour = solar_ut_rise.hours;
        rise->ut_minute = solar_ut_rise.minutes;
        rise->ut_second = solar_ut_rise.seconds;
        struct ln_zonedate solar_local_rise;
        ln_get_local_date(solar_rst.rise, &solar_local_rise);
        rise->local_year = solar_local_rise.years;
        rise->local_month = solar_local_rise.months;
        rise->local_day = solar_local_rise.days;
        rise->local_hour = solar_local_rise.hours;
        rise->local_minute = solar_local_rise.minutes;
        rise->local_second = solar_local_rise.seconds;

        /* is the moon above the horizon when the sun rises? */
        struct ln_equ_posn moon_equ_at_sunrise;
        ln_get_lunar_equ_coords(solar_rst.rise, &moon_equ_at_sunrise);
        struct ln_hrz_posn moon_hrz_at_sunrise;
        ln_get_hrz_from_equ(&moon_equ_at_sunrise, &observer, solar_rst.rise,
                &moon_hrz_at_sunrise);

        if (moon_hrz_at_sunrise.alt >= 0.)
            rise->moon_fraction = ln_get_lunar_disk(solar_rst.rise);
        else
            rise->moon_fraction = -1.;

        rise->jd = solar_rst.rise;
        strcpy(rise->label, "Sun Rise");
    }

/*    struct ln_equ_posn moon_equ_at_sunset, moon_equ_at_sunrise;
    ln_get_lunar_equ_coords(solar_rst.set, &moon_equ_at_sunset);
    ln_get_lunar_equ_coords(solar_rst.rise, &moon_equ_at_sunrise);
    struct ln_hrz_posn moon_hrz_at_sunset, moon_hrz_at_sunrise;
    ln_get_hrz_from_equ(&moon_equ_at_sunset, &observer, solar_rst.set,
            &moon_hrz_at_sunset);
    ln_get_hrz_from_equ(&moon_equ_at_sunrise, &observer, solar_rst.rise,
            &moon_hrz_at_sunrise);
    printf("moon_hrz_at_sunset: %f %f, moon_hrz_at_sunrise: %f %f\n",
            moon_hrz_at_sunset.az, moon_hrz_at_sunset.alt,
            moon_hrz_at_sunrise.az, moon_hrz_at_sunrise.alt); */


    return 0;
}

void print_ephem_data(const struct ephem_data *data, int ut_time,
        int csv, int verbose)
{
    char delimit = ' ';
    if (csv)
        delimit = ',';

    if (!csv)
        printf("%9s: ", data->label);
    if (ut_time == 1)
    {
        printf("%04d-%02d-%02d %02d:%02d:%07.4f%c", 
                data->ut_year, data->ut_month, data->ut_day,
                data->ut_hour, data->ut_minute, data->ut_second, delimit);
    }
    else
    {
        printf("%04d-%02d-%02d %02d:%02d:%07.4f%c", 
                data->local_year, data->local_month, data->local_day,
                data->local_hour, data->local_minute, data->local_second,
                delimit);
    }

    if (csv)
    {
        /* setting width puts leading space in the output */
        printf("%.4f", data->moon_fraction);
    }
    else
    {
        printf("(");
        printf("%7.4f", data->moon_fraction);
    }

    if (!csv)
        printf(")");

    if (verbose)
        printf(" jd: %f", data->jd);
    /* don't print newline when in csv mode, calling function
       will do that */
    if (!csv)
        printf("\n");
}

void print_csv(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time)
{
    print_ephem_data(sun_set, ut_time, 1, 0);
    printf(",");
    print_ephem_data(sun_rise, ut_time, 1, 0);
    printf(",");
    print_ephem_data(moon_set, ut_time, 1, 0);
    printf(",");
    print_ephem_data(moon_rise, ut_time, 1, 0);
    printf("\n");
}
        
void print_ordered(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time)
{
    struct ephem_data d[4] = {*sun_set, *sun_rise, *moon_set, *moon_rise};
    qsort(d, 4, sizeof(struct ephem_data), ephem_compar);
    for (int i = 0; i < 4; i++)
        print_ephem_data(&d[i], ut_time, 0, 1);
}

int ephem_compar(const void *a, const void *b)
{
    if (((struct ephem_data *)a)->jd < ((struct ephem_data *)b)->jd)
        return -1;
    else if (((struct ephem_data *)a)->jd > ((struct ephem_data *)b)->jd)
        return 1;
    else
        return 0;
}
