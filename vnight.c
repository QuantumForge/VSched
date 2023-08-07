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

/* gcc -o vnight vnight.c -I/Users/whanlon/starlink/include/star -I/Users/whanlon/local/include -L/Users/whanlon/starlink/lib -L/Users/whanlon/local/lib/ -lc -lsla -lnova */

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
    printf("  -z, --zone  Print time zone data in output.\n");
    printf("\nYear must be four digits. Date is UT date.\n\n");
    printf("Event times are UT unless -l switch is used.\n");

    return;
}

/* structure to hold a sun rise, sun set, moon rise, moon set event time.
   fraction of the moon's disk that is illuminated at the time of the
   event is also stored. if the moon is below the horizon, moon_illum
   is < 0.
 */
struct ephem_data
{
    /* if used for a sun event, moon_illum is moon fraction at the
       time stored here. if the moon is not above the horizon, then
       fraction is < 0. moon_alt is the altitude of the moon on date. */
    struct ln_date date;
    double moon_illum; /* fraction of the moon that is illuminated [0 - 1] */
    double moon_alt; /* altitude of moon */
    double jd; /* julian date */
    char label[16];
};

int get_moon_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set);
int get_sun_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set);
void get_moon_alt_and_illum(double jd, struct ln_lnlat_posn *observer,
        double *alt, double *illum);
void print_ephem_data(struct ephem_data *data, int ut_time,
        int csv, int verbose, int tz);
void print_csv(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time, int tz);
void print_ordered(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time, int tz);
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
    int opt_tz = 0;
    static struct option longopts[] =
    {
        {"csv",     no_argument,     NULL,   'c'},
        {"help",    no_argument,     NULL,   'h'},
        {"local",   no_argument,     NULL,   'l'},
        {"zone",    no_argument,     NULL,   'z'},
        {NULL,      0,               NULL,   0}
    };

    int c;
    while((c = getopt_long(argc, argv, "chlz", longopts, NULL)) != -1)
    {
        switch (c)
        {
            case 'c':
               opt_csv = 1;
               break; 
            case 'l':
               opt_ut = 0;
               break;
            case 'z':
               opt_tz = 1;
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
    /* print_ephem_data(&moon_rise, opt_ut, 0, 1, opt_tz); */
    /* print_ephem_data(&moon_set, opt_ut, 0, 1, opt_tz); */

    /* get the sun rise and set times */
    struct ephem_data sun_rise;
    struct ephem_data sun_set;
    get_sun_rise_set(ut_year, ut_month, ut_day, &sun_rise, &sun_set);
    /* print_ephem_data(&sun_rise, opt_ut, 0, 1, opt_tz); */
    /* print_ephem_data(&sun_set, opt_ut, 0, 1, opt_tz); */

    if (opt_csv)
        print_csv(&sun_set, &sun_rise, &moon_set, &moon_rise, opt_ut, opt_tz);
    else
        print_ordered(&sun_set, &sun_rise, &moon_set, &moon_rise, opt_ut,
                opt_tz);

    exit(EXIT_SUCCESS);
}


int get_moon_rise_set(unsigned long year, unsigned long month,
        unsigned long day, struct ephem_data *rise, struct ephem_data *set)
{
    /* this function is written to mimic how loggen routines determine
       event times. those routines do not set observer elevation or
       make correction for refraction. */
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

    rise->jd = lunar_rst.rise;
    ln_get_date(lunar_rst.rise, &(rise->date));
    rise->moon_illum = ln_get_lunar_disk(lunar_rst.rise);
    strcpy(rise->label, "Moon Rise");

    set->jd = lunar_rst.set;
    ln_get_date(lunar_rst.set, &(set->date));
    set->moon_illum = ln_get_lunar_disk(lunar_rst.set);
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
    /* this function is written to mimic how loggen routines determine
       event times. those routines do not set observer elevation or
       make correction for refraction. */
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
        ln_get_date(solar_rst.set, &(set->date));
        
        set->jd = solar_rst.set;
        /* is the moon above the horizon when the sun sets? */
        get_moon_alt_and_illum(solar_rst.set, &observer, &(set->moon_alt),
                &(set->moon_illum));

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
        ln_get_date(solar_rst.rise, &(rise->date));

        rise->jd = solar_rst.rise;
        /* is the moon above the horizon when the sun rises? */
        get_moon_alt_and_illum(solar_rst.rise, &observer, &(rise->moon_alt),
                &(rise->moon_illum));

        strcpy(rise->label, "Sun Rise");
    }

    return 0;
}

void get_moon_alt_and_illum(double jd, struct ln_lnlat_posn *observer,
        double *alt, double *illum)
{
    struct ln_equ_posn equ_posn;
    ln_get_lunar_equ_coords(jd, &equ_posn);
    struct ln_hrz_posn hrz_posn;
    ln_get_hrz_from_equ(&equ_posn, observer, jd, &hrz_posn);

    *alt = hrz_posn.alt;
    *illum = ln_get_lunar_disk(jd);

    if (*alt < 0.)
        *illum *= -1.;

    return;
}

void print_ephem_data(struct ephem_data *data, int ut_time,
        int csv, int verbose, int tz)
{
    char delimit = ' ';
    if (csv)
        delimit = ',';

    struct ln_date date;
    if (!csv)
        printf("%9s: ", data->label);
    if (ut_time == 1)
    {
        printf("%04d-%02d-%02d %02d:%02d:%07.4f", 
                (data->date).years, (data->date).months, (data->date).days,
                (data->date).hours, (data->date).minutes, (data->date).seconds);
        if (tz)
            printf("+00");
        printf("%c", delimit);
    }
    else
    {
        struct ln_zonedate mst;
        ln_date_to_zonedate(&(data->date), &mst, -7*3600);
        printf("%04d-%02d-%02d %02d:%02d:%07.4f", 
                mst.years, mst.months, mst.days,
                mst.hours, mst.minutes, mst.seconds);
        if (tz)
            printf("-07");
        printf("%c", delimit);
    }

    if (csv)
    {
        /* setting width puts leading space in the output */
        printf("%.4f%c", data->moon_illum, delimit);
        printf("%.4f", data->moon_alt);
    }
    else
    {
        printf("(");
        printf("%7.4f%c", data->moon_illum, delimit);
        printf("%9.4f", data->moon_alt);
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
        struct ephem_data *moon_rise, int ut_time, int tz)
{
    print_ephem_data(sun_set, ut_time, 1, 0, tz);
    printf(",");
    print_ephem_data(sun_rise, ut_time, 1, 0, tz);
    printf(",");
    print_ephem_data(moon_set, ut_time, 1, 0, tz);
    printf(",");
    print_ephem_data(moon_rise, ut_time, 1, 0, tz);
    printf("\n");
}
        
void print_ordered(struct ephem_data *sun_set,
        struct ephem_data *sun_rise, struct ephem_data *moon_set,
        struct ephem_data *moon_rise, int ut_time, int tz)
{
    struct ephem_data d[4] = {*sun_set, *sun_rise, *moon_set, *moon_rise};
    qsort(d, 4, sizeof(struct ephem_data), ephem_compar);
    for (int i = 0; i < 4; i++)
        print_ephem_data(&d[i], ut_time, 0, 1, tz);
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
