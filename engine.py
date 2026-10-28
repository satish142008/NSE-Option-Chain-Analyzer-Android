"""
Background engine: fetches the NSE option chain, runs the analysis, paper-trades the Open Interest
signal, sends notifications and writes state.json for the UI. Has no Kivy dependency, so it can run
inside an Android foreground service (service.py) or in a thread on a desktop.
"""
import datetime
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

import nse_core as core

N_COLS = 9


class Engine:
    def __init__(self, base_dir: str, export_dir: str, notifier, client=None):
        self.base = base_dir
        self.export_dir = export_dir
        self.notifier = notifier
        self.client = client or core.NseClient()
        self.exporter = core.CsvExporter(export_dir)
        self.flags: Dict[str, bool] = {k: False for k in
                                       ('live_export', 'save_oc', 'notifications', 'auto_stop', 'warn_late_update')}
        self.popups: List[Dict[str, Any]] = []
        self._popup_id = 0
        self._fail_shown = False
        self.status_text = "Starting..."
        self.finished = False
        self.finish_reason = ""
        self.next_wait = 60

    # ---- small helpers -----------------------------------------------------
    def p(self, name: str) -> str:
        return os.path.join(self.base, name)

    def log(self, msg: str) -> None:
        try:
            path = os.path.join(self.export_dir, 'NSE-OCA-service.log')
            if os.path.isfile(path) and os.path.getsize(path) > 500_000:
                os.remove(path)
            with open(path, 'a') as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def popup(self, title: str, message: str, action: Optional[str] = None) -> None:
        self._popup_id += 1
        self.popups.append({'id': int(time.time() * 1000) * 100 + self._popup_id % 100, 'title': title,
                            'msg': message, 'action': action})
        self.popups = self.popups[-20:]

    def notify(self, title: str, message: str) -> None:
        if not self.flags.get('notifications'):
            return
        try:
            self.notifier.notify(title, message)
        except Exception as err:
            self.log(f"notify failed: {err}")

    def refresh_flags(self) -> None:
        try:
            self.flags = core.read_flags(self.p('NSE-OCA.ini'))
        except Exception as err:
            self.log(f"flags: {err}")

    # ---- session -----------------------------------------------------------
    def setup(self, session: Dict[str, Any]) -> None:
        self.session = session
        self.run_id = session['run_id']
        self.mode = session['mode']
        self.symbol = session['symbol']
        self.expiry = session['expiry']
        self.sp = int(session['sp'])
        self.seconds = int(session['seconds'])
        self.units = core.units_for(self.mode)
        self.out_cols, self.csv_headers = core.column_headers(self.mode)
        self.rows: List[list] = []
        self.colors: List[list] = []
        self.values: Dict[str, str] = {}
        self.label_bg: Dict[str, Optional[str]] = {}
        self.prev: Dict[str, Any] = {}
        self.old_values: List[Any] = [None] * N_COLS
        self.first_run = True
        self.prev_date: Optional[datetime.date] = None
        self.prev_time: Optional[datetime.time] = None
        self.trader = core.PaperTrader()
        self.last_price: Optional[float] = None
        st = core.read_json(self.p('state.json'), {})
        if st.get('run_id') == self.run_id and 'eng' in st:  # resume after a service restart / Start after Stop
            e = st['eng']
            self.rows, self.colors = st.get('rows', []), st.get('colors', [])
            self.values, self.label_bg = st.get('values', {}), st.get('label_bg', {})
            self.prev, self.old_values = e.get('prev', {}), e.get('old_values', [None] * N_COLS)
            self.first_run = e.get('first_run', True)
            if e.get('prev_date'):
                self.prev_date = datetime.date.fromisoformat(e['prev_date'])
                self.prev_time = datetime.time.fromisoformat(e['prev_time'])
            self.trader.load(st.get('trade', {}))
            self.last_price = st.get('last_price')
            self._popup_id = 0
            self.popups = st.get('popups', [])
        self.finished = False

    def save_state(self) -> None:
        state = {
            'run_id': self.run_id, 'version': int(time.time() * 1000), 'ts': time.time(),
            'status': self.status_text, 'finished': self.finished, 'finish_reason': self.finish_reason,
            'symbol': self.symbol, 'expiry': self.expiry, 'sp': self.sp, 'mode': self.mode,
            'rows': self.rows, 'colors': self.colors, 'values': self.values, 'label_bg': self.label_bg,
            'trade': self.trader.to_dict(), 'last_price': self.last_price, 'popups': self.popups,
            'eng': {'first_run': self.first_run, 'prev': self.prev, 'old_values': self.old_values,
                    'prev_date': self.prev_date.isoformat() if self.prev_date else None,
                    'prev_time': self.prev_time.isoformat() if self.prev_time else None},
        }
        try:
            core.write_json(self.p('state.json'), state)
        except Exception as err:
            self.log(f"save_state: {err}")

    def set_status(self, text: str) -> None:
        self.status_text = text
        self.save_state()

    def should_stop(self) -> bool:
        ctl = core.read_json(self.p('control.json'), {})
        if ctl.get('paused') or ctl.get('quit'):
            return True
        sess = core.read_json(self.p('session.json'), {})
        return (not sess.get('active')) or sess.get('run_id') != self.run_id

    # ---- main loop -----------------------------------------------------------
    def run(self) -> str:
        session = core.read_json(self.p('session.json'), {})
        ctl = core.read_json(self.p('control.json'), {})
        if not session.get('active') or ctl.get('paused') or ctl.get('quit'):
            return 'idle'
        self.setup(session)
        self.refresh_flags()
        try:
            self.client.refresh_cookies()
        except Exception as err:
            self.log(f"initial cookies: {err}")
        self.set_status("Running in background...")
        while True:
            if self.should_stop():
                self.set_status("Stopped")
                return 'stopped'
            try:
                result = self.tick()
            except Exception as err:
                self.log(traceback.format_exc())
                self.set_status(f"Error: {err}")
                result = 'retry'
            if result == 'finish':
                return 'finished'
            end = time.time() + (self.next_wait if result != 'retry' else min(self.seconds, 30))
            while time.time() < end:
                if self.should_stop():
                    self.set_status("Stopped")
                    return 'stopped'
                time.sleep(1)

    def finish(self, reason: str, status: str) -> str:
        self.finished = True
        self.finish_reason = reason
        self.status_text = status
        self.save_state()
        return 'finish'

    # ---- one refresh (port of Nse.main / get_dataframe / set_values) ----------------
    def tick(self) -> str:
        self.refresh_flags()
        self.next_wait = self.seconds
        self.set_status("Fetching data...")
        try:
            if self.first_run:
                data = self.client.fetch_chain_first(self.mode, self.symbol, self.expiry)
            else:
                data = self.client.fetch_chain_refresh(self.mode, self.symbol, self.expiry)
        except Exception as err:
            print(err, sys.exc_info()[0], "1")
            self.log(f"fetch failed: {err}")
            try:
                self.client.refresh_cookies()
            except Exception:
                pass
            self.set_status(f"Fetch failed: {err}")
            if not self._fail_shown:
                self._fail_shown = True
                self.popup("Error", "Error in fetching data.\nWill keep retrying in the background.")
                self.save_state()
            return 'retry'
        self._fail_shown = False
        if data is None:
            self.set_status("No data received from NSE, retrying...")
            return 'ok'
        try:
            rows, timestamp, points = core.parse_chain(data, self.expiry)
        except core.InvalidExpiry:
            self.popup("Error", "Invalid Expiry Date.\nPlease restart and enter a new Expiry Date.", 'login')
            return self.finish('invalid_expiry', "Invalid expiry date")
        self.last_price = points
        str_time = timestamp.split(" ")[1]
        cur_date = datetime.datetime.strptime(timestamp.split(" ")[0], '%d-%b-%Y').date()
        cur_time = datetime.datetime.strptime(str_time, '%H:%M:%S').time()
        if self.first_run or (self.prev_date is not None and cur_date > self.prev_date):
            self.prev_date, self.prev_time = cur_date, cur_time
        elif cur_date == self.prev_date:
            if cur_time > self.prev_time:
                diff = core.minutes_since(self.prev_time, cur_time)
                if diff >= core.TIME_DIFFERENCE_FACTOR and self.flags.get('warn_late_update'):
                    self.popup("Late Update",
                               f"The data from the server was last updated about {int(diff)} minutes ago.")
                self.prev_time = cur_time
            else:
                self.set_status(f"No new data from server (last update {str_time})")
                return 'ok'
        try:
            a = core.analyze(rows, self.sp, self.mode)
        except core.IncorrectStrike:
            self.popup("Error", "Incorrect Strike Price.\nPlease enter correct Strike Price.", 'login')
            return self.finish('incorrect_strike', "Incorrect strike price")
        self.apply(a, str_time, points)
        if self.flags.get('save_oc'):
            try:
                self.exporter.dump_full(self.symbol, self.expiry, rows)
            except PermissionError as err:
                self.log(f"dump: {err}")
                self.popup("Export Failed", "Failed to access the Full CSV file.\nPermission Denied.")
            except Exception as err:
                self.log(f"dump: {err}")
        self.first_run = False
        today = datetime.datetime.strptime(time.strftime("%d-%b-%Y", time.localtime()), "%d-%b-%Y").date()
        if str_time == '15:30:00' and self.flags.get('auto_stop') and self.prev_date == today:
            self.popup("Market Closed", "Retrieving new data has been stopped.")
            self.notify("Market Closed", f"{self.symbol}: retrieving new data has been stopped.")
            return self.finish('market_closed', "Market closed")
        self.status_text = f"Updated: server time {str_time}"
        self.save_state()
        return 'ok'

    def changed(self, key: str, new: Any, title: str) -> Optional[Any]:
        old = self.prev.get(key)
        self.prev[key] = new
        if not self.first_run and old is not None and old != new:
            return old
        return None

    def apply(self, a, str_time: str, points: float) -> None:
        v = self.values
        v.update({'ub_oi1': str(a.max_call_oi), 'ub_sp1': str(a.max_call_oi_sp),
                  'ub_oi2': str(a.max_call_oi_2), 'ub_sp2': str(a.max_call_oi_sp_2),
                  'lb_oi1': str(a.max_put_oi), 'lb_sp1': str(a.max_put_oi_sp),
                  'lb_oi2': str(a.max_put_oi_2), 'lb_sp2': str(a.max_put_oi_sp_2)})
        for key, val, title in (('ub1', a.max_call_oi_sp, "Upper Boundary Strike Price"),
                                ('ub2', a.max_call_oi_sp_2, "Upper Boundary Strike Price 2"),
                                ('lb1', a.max_put_oi_sp, "Lower Boundary Strike Price"),
                                ('lb2', a.max_put_oi_sp_2, "Lower Boundary Strike Price 2")):
            old = self.changed(key, val, title)
            if old is not None:
                self.notify(f"{title} changed for {self.symbol}", f"Changed from {old} to {val}")

        labels = core.compute_labels(a)
        titles = {'call_itm': "Call ITM", 'put_itm': "Put ITM", 'call_exits': "Call Exits",
                  'put_exits': "Put Exits"}
        for key, (text, bg) in labels.items():
            v[key] = str(text)
            self.label_bg[key] = bg
            if key == 'oi':
                old = self.changed('oi', text, "Open Interest")
                if old is not None:
                    self.on_oi_flip(old, text, points, str_time)
            elif key in titles:
                old = self.changed(key, text, titles[key])
                if old is not None:
                    self.notify(f"{titles[key]} changed for {self.symbol}", f"Changed from {old} to {text}")

        values = [str_time, points, a.call_sum, a.put_sum, a.difference, a.call_boundary,
                  a.put_boundary, a.call_itm, a.put_itm]
        colors = [''] * N_COLS
        for col in range(1, N_COLS):
            colors[col] = core.trend_color(col, values[col], self.old_values[col]) or ''
            self.old_values[col] = values[col]
        self.rows.append(values)
        self.colors.append(colors)
        if self.flags.get('live_export'):
            try:
                self.exporter.ensure_header(self.symbol, self.expiry, self.csv_headers)
                self.exporter.export_row(self.symbol, self.expiry, values)
            except PermissionError as err:
                self.log(f"live export: {err}")
                self.popup("Export Failed", "Failed to access the live export CSV.\nPermission Denied.")
            except Exception as err:
                self.log(f"live export: {err}")

    def on_oi_flip(self, old: str, new: str, price: float, str_time: str) -> None:
        trade = self.trader.on_signal(new, price, str_time)
        msg = f"Open Interest: {old} -> {new}"
        if trade:
            msg += f"\nPAPER TRADE: {trade['action']} {self.symbol} @ {price}"
            c = trade['closed']
            if c:
                msg += f"\nClosed {c['side']} {c['entry']} -> {c['exit']}: {core.signed(c['pnl'])} pts"
                msg += f" (total {core.signed(trade['total'])})"
            self._log_trade(trade, new)
        self.notify(f"{self.symbol} turned {new}", msg)

    def _log_trade(self, trade: Dict[str, Any], signal: str) -> None:
        path = os.path.join(self.export_dir, f"NSE-OCA-PaperTrades-{self.symbol}-{self.expiry}.csv")
        try:
            new = not os.path.isfile(path)
            import csv
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["Time", "Signal", "Action", "Price", "Closed Side", "Closed Entry",
                                "Closed PnL (pts)", "Total PnL (pts)"])
                c = trade['closed'] or {}
                w.writerow([trade['time'], signal, trade['action'], trade['price'], c.get('side', ''),
                            c.get('entry', ''), c.get('pnl', ''), trade['total']])
        except Exception as err:
            self.log(f"trade log: {err}")


def run_service() -> None:
    """Entry point for the Android foreground service."""
    base, export = core.shared_dir(), core.external_dir()
    wake = None
    ctx = None
    try:
        from jnius import autoclass
        ctx = autoclass('org.kivy.android.PythonService').mService
        pm = ctx.getSystemService(autoclass('android.content.Context').POWER_SERVICE)
        wake = pm.newWakeLock(autoclass('android.os.PowerManager').PARTIAL_WAKE_LOCK, 'nseoca:engine')
        wake.acquire()
        from android_notify import AndroidNotifier
        notifier = AndroidNotifier(base)
    except Exception:
        notifier = _PrintNotifier()
        with open(os.path.join(export, 'NSE-OCA-service.log'), 'a') as f:
            f.write(traceback.format_exc() + "\n")
    engine = Engine(base, export, notifier)
    try:
        engine.run()
    except Exception:
        engine.log(traceback.format_exc())
    finally:
        try:
            if wake is not None:
                wake.release()
            if ctx is not None:
                ctx.stopSelf()
        except Exception:
            pass


class _PrintNotifier:
    def notify(self, title: str, message: str) -> None:
        print(f"[NOTIFY] {title}: {message}")
