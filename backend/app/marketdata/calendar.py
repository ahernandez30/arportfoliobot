"""The New York Stock Exchange calendar the strategy script uses (v9.36), as rules: full-day closures
that can fall Tuesday to Friday, and 13:00 early closes. The same rules are in the script
(f_feriado, f_temprano), so the site and TradingView agree; a closure announced at short notice
must be added in both."""
from datetime import date, timedelta

# Closures for one day or more, Tuesday to Friday, since 1994 (f_feriado's "cierres especiales").
SPECIAL_CLOSURES = {date(1994, 4, 27), date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
                    date(2004, 6, 11), date(2007, 1, 2), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}


def easter(y: int) -> date:
    """f_pascua: Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    return date(y, (h + l_ - 7 * m + 114) // 31, (h + l_ - 7 * m + 114) % 31 + 1)


def nyse_closed(d: date) -> bool:
    """f_feriado: the exchange is closed all day. Only Tuesday to Friday matter: the days that can
    follow another in the same week."""
    y, mo, dd, fri = d.year, d.month, d.day, d.weekday() == 4
    holiday = ((mo, dd) == (1, 1) or d == easter(y) - timedelta(days=2) or (y >= 2022 and (mo, dd) == (6, 19))
               or (mo, dd) == (7, 4) or (mo, dd) == (12, 25) or (mo == 11 and d.weekday() == 3 and 22 <= dd <= 28))
    # A holiday on a Saturday closes the Friday before (except New Year's Day).
    saturday = fri and ((y >= 2022 and (mo, dd) == (6, 18)) or (mo, dd) == (7, 3) or (mo, dd) == (12, 24))
    return holiday or saturday or d in SPECIAL_CLOSURES


def nyse_early_close(d: date) -> bool:
    """f_temprano: closes at 13:00. The Friday after Thanksgiving, and 24 December and 3 July from
    Monday to Thursday."""
    mon_thu = d.weekday() <= 3
    return ((d.month == 11 and d.weekday() == 4 and 23 <= d.day <= 29) or ((d.month, d.day) == (12, 24) and mon_thu)
            or ((d.month, d.day) == (7, 3) and mon_thu))


def session_close_minute(d: date) -> int:
    """Minutes after midnight New York when the regular session ends that day."""
    return 780 if nyse_early_close(d) else 960
