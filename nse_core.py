"""
NSE Option Chain Analyzer - core logic (no UI).

Ported from Varun Shanbhag's Python-NSE-Option-Chain-Analyzer v5.8 (GPLv3).
The analysis maths, labels, colours and request flow are identical to the
desktop version; pandas/tkinter were removed so it runs on Android.
"""
import configparser
import csv
import datetime
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

VERSION = "5.8"
ORIGINAL_REPO = "VarunS2002/Python-NSE-Option-Chain-Analyzer"

INTERVALS: List[int] = [1, 2, 3, 5, 10, 15]
VALID_SECONDS = (60, 120, 180, 300, 600, 900)
TIME_DIFFERENCE_FACTOR = 5

URL_OC = "https://www.nseindia.com/option-chain"
URL_CONTRACT_INFO = "https://www.nseindia.com/api/option-chain-contract-info?symbol="
URL_INDEX_DATA = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={}&expiry={}"
URL_STOCK_DATA = "https://www.nseindia.com/api/option-chain-v3?type=Equity&symbol={}&expiry={}"
URL_SYMBOLS = "https://www.nseindia.com/api/underlying-information"

HEADERS: Dict[str, str] = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/130.0.0.0 Safari/537.36',
    'accept-language': 'en,gu;q=0.9,hi;q=0.8',
    'accept-encoding': 'gzip, deflate'}

RED = "#e53935"
GREEN = "#00e676"

MERGED_COLUMNS = ['Open Interest', 'Change in Open Interest', 'Traded Volume', 'Implied Volatility',
                  'Last Traded Price', 'Net Change', 'Bid Quantity', 'Bid Price', 'Ask Price',
                  'Ask Quantity', 'Strike Price', 'Bid Quantity', 'Bid Price', 'Ask Price',
                  'Ask Quantity', 'Net Change', 'Last Traded Price', 'Implied Volatility',
                  'Traded Volume', 'Change in Open Interest', 'Open Interest']
_COLUMNS_CE = ['openInterest', 'changeinOpenInterest', 'totalTradedVolume', 'impliedVolatility',
               'lastPrice', 'change', 'buyQuantity1', 'buyPrice1', 'sellPrice1', 'sellQuantity1',
               'strikePrice']
_COLUMNS_PE = ['strikePrice', 'buyQuantity1', 'buyPrice1', 'sellPrice1', 'sellQuantity1', 'change',
               'lastPrice', 'impliedVolatility', 'totalTradedVolume', 'changeinOpenInterest',
               'openInterest']


class InvalidExpiry(Exception):
    """No option chain rows for the selected expiry."""


class IncorrectStrike(Exception):
    """The strike price is not present in the option chain."""


def units_for(option_mode: str) -> str:
    return 'in K' if option_mode == 'Index' else 'in 10s'


def round_factor_for(option_mode: str) -> int:
    return 1000 if option_mode == 'Index' else 10


def column_headers(option_mode: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    u = units_for(option_mode)
    output = ('Time', 'Value', f'Call Sum\n({u})', f'Put Sum\n({u})', f'Difference\n({u})',
              f'Call Boundary\n({u})', f'Put Boundary\n({u})', 'Call ITM', 'Put ITM')
    csv_headers = ('Time', 'Value', f'Call Sum ({u})', f'Put Sum ({u})', f'Difference ({u})',
                   f'Call Boundary ({u})', f'Put Boundary ({u})', 'Call ITM', 'Put ITM')
    return output, csv_headers


# --------------------------------------------------------------------------- analysis
def parse_chain(json_data: Dict[str, Any], expiry_date: str) -> Tuple[List[list], str, float]:
    """Equivalent of the pandas CE/PE inner merge. Returns (rows, timestamp, underlying value)."""
    records = json_data['records']
    ce_values = [d['CE'] for d in records['data']
                 if "CE" in d and d['expiryDates'].lower() == expiry_date.lower()]
    pe_values = [d['PE'] for d in records['data']
                 if "PE" in d and d['expiryDates'].lower() == expiry_date.lower()]
    if not ce_values or not pe_values:
        raise InvalidExpiry()
    points = pe_values[0]['underlyingValue']
    if points == 0:
        for item in pe_values:
            if item['underlyingValue'] != 0:
                points = item['underlyingValue']
                break
    pe_by_strike: Dict[Any, List[dict]] = {}
    for pe in pe_values:
        pe_by_strike.setdefault(pe.get('strikePrice'), []).append(pe)
    rows: List[list] = []
    for ce in ce_values:
        for pe in pe_by_strike.get(ce.get('strikePrice'), []):
            # pandas merges on strikePrice, so the key column appears once (21 columns)
            rows.append([ce.get(c, 0) for c in _COLUMNS_CE] + [pe.get(c, 0) for c in _COLUMNS_PE[1:]])
    if not rows:
        raise InvalidExpiry()
    return rows, records['timestamp'], points


def _get(rows: List[list], idx: int, col: int) -> int:
    """pandas Series.get(label) semantics: out-of-range (incl. negative) -> default 0."""
    if 0 <= idx < len(rows):
        return int(rows[idx][col])
    return 0


class Analysis:
    pass


def analyze(rows: List[list], sp: int, option_mode: str) -> Analysis:
    rf = round_factor_for(option_mode)
    a = Analysis()
    call_oi_list = [int(r[0]) for r in rows]
    call_oi_index = call_oi_list.index(max(call_oi_list))
    a.max_call_oi = round(max(call_oi_list) / rf, 1)
    a.max_call_oi_sp = float(rows[call_oi_index][10])

    put_oi_list = [int(r[20]) for r in rows]
    put_oi_index = put_oi_list.index(max(put_oi_list))
    a.max_put_oi = round(max(put_oi_list) / rf, 1)
    a.max_put_oi_sp = float(rows[put_oi_index][10])

    sp_range_list = [float(rows[i][10]) for i in range(put_oi_index, call_oi_index + 1)]

    if a.max_call_oi_sp == a.max_put_oi_sp or not sp_range_list:
        # (empty range = put wall above call wall: the desktop version crashes here, so fall back)
        a.max_call_oi_2 = a.max_call_oi
        a.max_call_oi_sp_2 = a.max_call_oi_sp
        a.max_put_oi_2 = a.max_put_oi
        a.max_put_oi_sp_2 = a.max_put_oi_sp
    elif len(sp_range_list) == 2:
        a.max_call_oi_2 = round(int(rows[put_oi_index][0]) / rf, 1)
        a.max_call_oi_sp_2 = a.max_put_oi_sp
        a.max_put_oi_2 = round(int(rows[call_oi_index][20]) / rf, 1)
        a.max_put_oi_sp_2 = a.max_call_oi_sp
    else:
        call_oi_list_2 = [int(rows[i][0]) for i in range(put_oi_index, call_oi_index)]
        call_oi_index_2 = put_oi_index + call_oi_list_2.index(max(call_oi_list_2))
        a.max_call_oi_2 = round(max(call_oi_list_2) / rf, 1)
        a.max_call_oi_sp_2 = float(rows[call_oi_index_2][10])

        put_oi_list_2 = [int(rows[i][20]) for i in range(put_oi_index + 1, call_oi_index + 1)]
        put_oi_index_2 = put_oi_index + 1 + put_oi_list_2.index(max(put_oi_list_2))
        a.max_put_oi_2 = round(max(put_oi_list_2) / rf, 1)
        a.max_put_oi_sp_2 = float(rows[put_oi_index_2][10])

    total_call_oi = sum(call_oi_list)
    total_put_oi = sum(put_oi_list)
    try:
        a.put_call_ratio = round(total_put_oi / total_call_oi, 2)
    except ZeroDivisionError:
        a.put_call_ratio = 0

    index = None
    for i, r in enumerate(rows):
        if r[10] == sp:
            index = i
            break
    if index is None:
        raise IncorrectStrike()

    c1 = _get(rows, index, 1)
    c2 = _get(rows, index + 1, 1)
    c3 = _get(rows, index + 2, 1)
    a.call_sum = round((c1 + c2 + c3) / rf, 1)
    if a.call_sum == 0:
        a.call_sum = 0.0
    a.call_boundary = round(c3 / rf, 1)

    p1 = _get(rows, index, 19)
    p2 = _get(rows, index + 1, 19)
    p3 = _get(rows, index + 2, 19)
    a.p4 = _get(rows, index + 4, 19)
    a.p5 = _get(rows, index + 4, 1)
    a.p6 = _get(rows, index - 2, 1)
    a.p7 = _get(rows, index - 2, 19)
    a.put_sum = round((p1 + p2 + p3) / rf, 1)
    a.put_boundary = round(p1 / rf, 1)
    a.difference = round(a.call_sum - a.put_sum, 1)
    if a.p5 == 0:
        a.call_itm = 0.0
    else:
        a.call_itm = round(a.p4 / a.p5, 1)
        if a.call_itm == 0:
            a.call_itm = 0.0
    if a.p7 == 0:
        a.put_itm = 0.0
    else:
        a.put_itm = round(a.p6 / a.p7, 1)
        if a.put_itm == 0:
            a.put_itm = 0.0
    return a


def itm_label(call_change: float, put_change: float) -> str:
    label = "No"
    if put_change > call_change:
        if put_change >= 0:
            if call_change <= 0:
                label = "Yes"
            elif put_change / call_change > 1.5:
                label = "Yes"
        else:
            if put_change / call_change < 0.5:
                label = "Yes"
    if call_change <= 0:
        label = "Yes"
    return label


def compute_labels(a: Analysis) -> Dict[str, Tuple[Any, Optional[str]]]:
    """Label text and background colour (None = default) exactly as the desktop app sets them."""
    out: Dict[str, Tuple[Any, Optional[str]]] = {}
    if a.call_sum >= a.put_sum:
        out['oi'] = ("Bearish", RED)
    else:
        out['oi'] = ("Bullish", GREEN)
    out['pcr'] = (a.put_call_ratio, GREEN if a.put_call_ratio >= 1 else RED)
    call = itm_label(call_change=a.p5, put_change=a.p4)
    out['call_itm'] = (call, None if call == "No" else GREEN)
    put = itm_label(call_change=a.p7, put_change=a.p6)
    out['put_itm'] = (put, None if put == "No" else RED)
    if a.call_boundary <= 0 or a.call_sum <= 0:
        out['call_exits'] = ("Yes", GREEN)
    else:
        out['call_exits'] = ("No", None)
    if a.put_boundary <= 0 or a.put_sum <= 0:
        out['put_exits'] = ("Yes", RED)
    else:
        out['put_exits'] = ("No", None)
    return out


# column -> (colour when value rises, colour when value falls), as in the desktop sheet highlighting
CELL_TREND = {1: (GREEN, RED), 2: (RED, GREEN), 3: (GREEN, RED), 4: (RED, GREEN),
              5: (RED, GREEN), 6: (GREEN, RED), 7: (GREEN, RED), 8: (RED, GREEN)}


def trend_color(column: int, new: float, old: Optional[float]) -> Optional[str]:
    if old is None or new == old:
        return None
    up, down = CELL_TREND[column]
    return up if new > old else down


def minutes_since(previous: datetime.time, current: datetime.time) -> float:
    """Same (quirky) difference formula as the desktop version."""
    diff = 0.0
    if current.hour > previous.hour:
        diff = (60 - previous.minute) + current.minute + ((60 - previous.second) + current.second) / 60
    elif current.hour == previous.hour:
        diff = current.minute - previous.minute + (current.second - previous.second) / 60
    return diff


# --------------------------------------------------------------------------- config
class Config:
    BOOLS = ('load_nse_icon', 'live_export', 'save_oc', 'notifications', 'auto_stop', 'update',
             'logging', 'warn_late_update')

    def __init__(self, path: str, indices: List[str], stocks: List[str], notifications_supported: bool):
        self.path = path
        self.indices = indices
        self.stocks = stocks
        self.notifications_supported = notifications_supported
        self.parser = configparser.ConfigParser()
        self.load_nse_icon = True
        self.index = indices[0]
        self.stock = stocks[0]
        self.option_mode = 'Index'
        self.seconds = 60
        self.live_export = self.save_oc = self.notifications = self.auto_stop = False
        self.update = True
        self.logging = False
        self.warn_late_update = False
        if not os.path.isfile(path):
            self._create(new=True)
        self._read()

    def _defaults(self) -> Dict[str, str]:
        return {'load_nse_icon': 'True', 'index': self.indices[0], 'stock': self.stocks[0],
                'option_mode': 'Index', 'seconds': '60', 'live_export': 'False', 'save_oc': 'False',
                'notifications': 'False', 'auto_stop': 'False', 'update': 'True', 'logging': 'False',
                'warn_late_update': 'False'}

    def _create(self, new: bool = False, corrupted: bool = False, attribute: Optional[str] = None) -> None:
        if new or corrupted:
            if corrupted and os.path.isfile(self.path):
                os.remove(self.path)
            self.parser = configparser.ConfigParser()
            self.parser.read(self.path)
            self.parser.add_section('main')
            for k, v in self._defaults().items():
                self.parser.set('main', k, v)
        elif attribute is not None:
            self.parser.set('main', attribute, self._defaults()[attribute])
        self.save()

    def save(self) -> None:
        with open(self.path, 'w') as f:
            self.parser.write(f)

    def set(self, key: str, value: Any) -> None:
        setattr(self, key, value)
        self.parser.set('main', key, str(value))
        self.save()

    def _read(self) -> None:
        try:
            self.parser.read(self.path)
            for key in self.BOOLS:
                try:
                    setattr(self, key, self.parser.getboolean('main', key))
                except (configparser.NoOptionError, ValueError) as err:
                    print(err, sys.exc_info()[0], "0")
                    self._create(attribute=key)
                    setattr(self, key, self.parser.getboolean('main', key))
            if not self.notifications_supported:
                self.notifications = False
            for key, valid in (('index', self.indices), ('stock', self.stocks)):
                try:
                    val = self.parser.get('main', key)
                    if val not in valid:
                        raise ValueError(f'{val} is not a valid {key}')
                    setattr(self, key, val)
                except (configparser.NoOptionError, ValueError) as err:
                    print(err, sys.exc_info()[0], "0")
                    self._create(attribute=key)
                    setattr(self, key, self.parser.get('main', key))
            try:
                self.option_mode = self.parser.get('main', 'option_mode')
                if self.option_mode not in ('Index', 'Stock'):
                    raise ValueError(f'{self.option_mode} is not a valid option mode')
            except (configparser.NoOptionError, ValueError) as err:
                print(err, sys.exc_info()[0], "0")
                self._create(attribute="option_mode")
                self.option_mode = self.parser.get('main', 'option_mode')
            try:
                self.seconds = self.parser.getint('main', 'seconds')
                if self.seconds not in VALID_SECONDS:
                    raise ValueError(f'{self.seconds} is not a refresh interval')
            except (configparser.NoOptionError, ValueError) as err:
                print(err, sys.exc_info()[0], "0")
                self._create(attribute="seconds")
                self.seconds = self.parser.getint('main', 'seconds')
        except (configparser.NoSectionError, configparser.MissingSectionHeaderError,
                configparser.DuplicateSectionError, configparser.DuplicateOptionError) as err:
            print(err, sys.exc_info()[0], "0")
            self._create(corrupted=True)
            self._read()


# --------------------------------------------------------------------------- network
class NseClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.cookies: Dict[str, str] = {}

    def close(self) -> None:
        self.session.close()

    def fetch_symbols(self) -> Tuple[List[str], List[str]]:
        request = self.session.get(URL_OC, headers=HEADERS, timeout=5)
        self.cookies = dict(request.cookies)
        response = self.session.get(URL_SYMBOLS, headers=HEADERS, timeout=5, cookies=self.cookies)
        json_data = response.json()
        return ([i['symbol'] for i in json_data['data']['IndexList']],
                [i['symbol'] for i in json_data['data']['UnderlyingList']])

    @staticmethod
    def data_url(option_mode: str, symbol: str, expiry_date: str) -> str:
        return (URL_INDEX_DATA if option_mode == 'Index' else URL_STOCK_DATA).format(symbol, expiry_date)

    def fetch_expiry_dates(self, option_mode: str, symbol: str) -> List[str]:
        """Login-screen request. Raises on failure (UI shows 'Error in fetching dates')."""
        response = self.session.get(URL_CONTRACT_INFO + symbol, headers=HEADERS, timeout=5,
                                    cookies=self.cookies)
        json_data = response.json()
        if not json_data:
            raise ValueError("empty response")
        return list(json_data['expiryDates'] if 'expiryDates' in json_data
                    else json_data['records']['expiryDates'])

    def fetch_chain_first(self, option_mode: str, symbol: str, expiry_date: str) -> Any:
        """First request in the main window. Raises on failure."""
        url = self.data_url(option_mode, symbol, expiry_date)
        response = self.session.get(url, headers=HEADERS, timeout=5, cookies=self.cookies)
        json_data = response.json()
        if not json_data:
            raise ValueError("empty response")
        return json_data

    def fetch_chain_refresh(self, option_mode: str, symbol: str, expiry_date: str) -> Optional[Any]:
        """Refresh request with the cookie-reset retry logic. Returns None on failure."""
        url = self.data_url(option_mode, symbol, expiry_date)
        request = response = None
        try:
            response = self.session.get(url, headers=HEADERS, timeout=5, cookies=self.cookies)
            if response.status_code == 401:
                self._reset_session()
                response = self.session.get(url, headers=HEADERS, timeout=5, cookies=self.cookies)
                print("reset cookies")
        except Exception as err:
            print(request, response)
            print(err, sys.exc_info()[0], "4")
            try:
                self._reset_session()
                response = self.session.get(url, headers=HEADERS, timeout=5, cookies=self.cookies)
                print("reset cookies")
            except Exception as err2:
                print(err2, sys.exc_info()[0], "5")
                return None
        try:
            json_data = response.json()
        except Exception as err:
            print(response)
            print(err, sys.exc_info()[0], "6")
            return None
        return json_data or None

    def _reset_session(self) -> None:
        self.session.close()
        self.session = requests.Session()
        request = self.session.get(URL_OC, headers=HEADERS, timeout=5)
        self.cookies = dict(request.cookies)


def latest_release_tag(repo: str) -> str:
    data = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                        headers=HEADERS, timeout=5).json()
    return data['tag_name']


def _ver(tag: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in tag.lower().lstrip('v').split('-')[0].split('.'))


def is_newer(latest_tag: str, current: str = VERSION) -> bool:
    return _ver(latest_tag) > _ver(current)


# --------------------------------------------------------------------------- csv
class CsvExporter:
    def __init__(self, directory: str):
        self.directory = directory

    def path(self, symbol: str, expiry: str, full: bool = False) -> str:
        return os.path.join(self.directory, f"NSE-OCA-{symbol}-{expiry}{'-Full' if full else ''}.csv")

    def ensure_header(self, symbol: str, expiry: str, headers) -> None:
        p = self.path(symbol, expiry)
        if not os.path.isfile(p):
            with open(p, "a", newline="") as f:
                csv.writer(f).writerow(headers)

    def export_table(self, symbol: str, expiry: str, headers, rows: List[list]) -> str:
        self.ensure_header(symbol, expiry, headers)
        p = self.path(symbol, expiry)
        with open(p, "a", newline="") as f:
            csv.writer(f).writerows(rows)
        return p

    def export_row(self, symbol: str, expiry: str, values) -> None:
        with open(self.path(symbol, expiry), "a", newline="") as f:
            csv.writer(f).writerow(values)

    def dump_full(self, symbol: str, expiry: str, rows: List[list]) -> None:
        with open(self.path(symbol, expiry, full=True), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(MERGED_COLUMNS)
            w.writerows(rows)


class LogRedirect:
    """Replacement for streamtologger: timestamps every stdout/stderr line into a file."""

    def __init__(self, path: str, level: str):
        self.path, self.level, self._buf = path, level, ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                with open(self.path, "a", buffering=1) as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')} - {self.level:5}] {line}\n")
        return len(text)

    def flush(self) -> None:
        pass


class DebugLog:
    def __init__(self, path: str):
        self.path = path
        self.stdout, self.stderr = sys.stdout, sys.stderr
        self.active = False

    def start(self) -> None:
        sys.stdout = LogRedirect(self.path, "INFO")
        sys.stderr = LogRedirect(self.path, "ERROR")
        self.active = True
        print('----------Logging Started----------')

    def stop(self) -> None:
        print('----------Logging Stopped----------')
        sys.stdout, sys.stderr = self.stdout, self.stderr
        self.active = False
