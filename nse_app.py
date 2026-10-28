"""
NSE-Option-Chain-Analyzer - Android (Kivy) port of Varun Shanbhag's desktop app v5.8 (GPLv3).
The UI only displays data; fetching/analysis/paper trading/notifications run in engine.py, inside an
Android foreground service, so everything keeps running when the app is closed or in the background.
"""
import os
import sys
import threading
import time
import traceback
import webbrowser

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex as hexc
from kivy.utils import platform

import nse_core as core

IS_ANDROID = platform == 'android'
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_CLASS = 'org.nseoca.nseoptionchain.ServiceNseservice'

BG = hexc("#0d0d0d")
PANEL = hexc("#1e1e1e")
HEADER = hexc("#2a2f33")
FG = (0.92, 0.92, 0.92, 1)
BLACK = (0, 0, 0, 1)
CELL_W = dp(88)
N_COLS = 9


# ----------------------------------------------------------------------------- android helpers
def android_activity():
    from jnius import autoclass
    return autoclass('org.kivy.android.PythonActivity').mActivity


def copy_to_downloads(path: str):
    """Copy a file into the public Downloads/NSE-OCA folder. Returns a location string or None."""
    if not IS_ANDROID:
        return None
    try:
        from jnius import autoclass
        name = os.path.basename(path)
        sdk = autoclass('android.os.Build$VERSION').SDK_INT
        with open(path, 'rb') as f:
            data = f.read()
        if sdk >= 29:
            ContentValues = autoclass('android.content.ContentValues')
            Downloads = autoclass('android.provider.MediaStore$Downloads')
            Cols = autoclass('android.provider.MediaStore$MediaColumns')
            values = ContentValues()
            values.put(Cols.DISPLAY_NAME, name)
            values.put(Cols.MIME_TYPE, 'text/csv')
            values.put(Cols.RELATIVE_PATH, 'Download/NSE-OCA')
            resolver = android_activity().getContentResolver()
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
            out = resolver.openOutputStream(uri)
            for i in range(0, len(data), 65536):
                out.write(bytearray(data[i:i + 65536]))
            out.close()
        else:
            import shutil
            target = '/storage/emulated/0/Download/NSE-OCA'
            os.makedirs(target, exist_ok=True)
            shutil.copy(path, os.path.join(target, name))
        return "Downloads/NSE-OCA"
    except Exception as err:
        print(err, "copy_to_downloads")
        return None


def open_battery_settings():
    try:
        from jnius import autoclass
        Intent = autoclass('android.content.Intent')
        Settings = autoclass('android.provider.Settings')
        android_activity().startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    except Exception as err:
        print(err)


def minimize_app():
    try:
        android_activity().moveTaskToBack(True)
    except Exception as err:
        print(err)


# ----------------------------------------------------------------------------- widgets
class WrapLabel(Label):
    def __init__(self, **kw):
        kw.setdefault('size_hint_y', None)
        super().__init__(**kw)
        self.bind(width=lambda *a: setattr(self, 'text_size', (self.width, None)))
        self.bind(texture_size=lambda *a: setattr(self, 'height', self.texture_size[1] + dp(6)))


class Cell(Label):
    """Label with a background colour (hex string, or None for the default panel colour)."""

    def __init__(self, text='', bg=None, bold=False, **kw):
        super().__init__(text=str(text), bold=bold, halign='center', valign='middle', **kw)
        self.bind(size=lambda *a: setattr(self, 'text_size', (self.width - dp(4), self.height)))
        with self.canvas.before:
            self._color = Color(*PANEL)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)
        self.set_bg(bg)

    def _sync(self, *a):
        self._rect.pos, self._rect.size = self.pos, self.size

    def set_bg(self, bg):
        if bg:
            self._color.rgba = hexc(bg)
            self.color = BLACK if bg == core.GREEN else (1, 1, 1, 1)
        else:
            self._color.rgba = PANEL
            self.color = FG


class PickerButton(Button):
    """Replacement for the desktop Combobox: tap to choose from a (searchable) list."""

    def __init__(self, values=None, value='', on_select=None, searchable=False, **kw):
        super().__init__(text=value, **kw)
        self.values = list(values or [])
        self.value = value
        self.on_select = on_select
        self.searchable = searchable

    def set_values(self, values, value=None):
        self.values = list(values)
        self.set_value(value if value is not None else (self.values[0] if self.values else ''))

    def set_value(self, value):
        self.value = value
        self.text = value

    def on_release(self):
        box = BoxLayout(orientation='vertical', spacing=dp(6))
        search = TextInput(multiline=False, size_hint_y=None, height=dp(44), hint_text="Search")
        listing = GridLayout(cols=1, size_hint_y=None, spacing=dp(2))
        listing.bind(minimum_height=listing.setter('height'))
        scroll = ScrollView()
        scroll.add_widget(listing)
        if self.searchable:
            box.add_widget(search)
        box.add_widget(scroll)
        popup = Popup(title="Select", content=box, size_hint=(.9, .8))

        def choose(value, *a):
            self.set_value(value)
            popup.dismiss()
            if self.on_select:
                self.on_select(value)

        def fill(*a):
            listing.clear_widgets()
            q = search.text.strip().lower()
            for v in [v for v in self.values if q in v.lower()][:300]:
                b = Button(text=v, size_hint_y=None, height=dp(44))
                b.bind(on_release=lambda btn, v=v: choose(v))
                listing.add_widget(b)

        search.bind(text=fill)
        fill()
        popup.open()


def crash_widget(text):
    scroll = ScrollView()
    scroll.add_widget(WrapLabel(text="App error - please send a screenshot:\n\n" + text, halign='left',
                                font_size=dp(12)))
    return scroll


def dialog(title, message, buttons, height=.4, auto_dismiss=False):
    """buttons: list of (label, callback or None)."""
    content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(8))
    scroll = ScrollView()
    scroll.add_widget(WrapLabel(text=message, halign='left'))
    content.add_widget(scroll)
    row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
    popup = Popup(title=title, content=content, size_hint=(.88, height), auto_dismiss=auto_dismiss)
    for label, cb in buttons:
        b = Button(text=label)

        def pressed(btn, cb=cb):
            popup.dismiss()
            if cb:
                cb()
        b.bind(on_release=pressed)
        row.add_widget(b)
    content.add_widget(row)
    popup.open()
    return popup


# ----------------------------------------------------------------------------- app
class NseApp(App):
    title = "NSE-Option-Chain-Analyzer"
    icon = "assets/icon.png"

    # ---- lifecycle -------------------------------------------------------
    def build(self):
        try:
            return self._build()
        except Exception:
            return crash_widget(traceback.format_exc())

    def _build(self):
        Window.clearcolor = BG
        Window.softinput_mode = 'below_target'
        Window.bind(on_keyboard=self.on_keyboard)
        self.client = core.NseClient()
        self.cfg = None
        self.run_id = None
        self._poll = None
        self._token = 0
        self.sm = ScreenManager(transition=NoTransition())
        loading = Screen(name='loading')
        box = BoxLayout(orientation='vertical', padding=dp(30), spacing=dp(12))
        box.add_widget(Image(source=os.path.join(APP_DIR, 'nse_logo.png'), size_hint_y=.5))
        box.add_widget(Label(text="NSE-Option-Chain-Analyzer\nLoading...", halign='center'))
        loading.add_widget(box)
        self.sm.add_widget(loading)
        return self.sm

    def on_start(self):
        try:
            self._on_start()
        except Exception:
            dialog("Startup error", traceback.format_exc(), [("OK", None)], height=.9)

    def _on_start(self):
        if IS_ANDROID:
            try:
                from android.permissions import request_permissions
                request_permissions(['android.permission.POST_NOTIFICATIONS',
                                     'android.permission.WRITE_EXTERNAL_STORAGE'])
            except Exception as err:
                print(err)
        self.base = core.shared_dir()
        self.export_dir = core.external_dir()
        os.makedirs(self.export_dir, exist_ok=True)
        self.exporter = core.CsvExporter(self.export_dir)
        session = core.read_json(self.pth('session.json'), {})
        symbols = core.read_json(self.pth('symbols.json'), None)
        if session.get('active') and symbols:  # app re-opened while a background session exists
            self.indices, self.stocks = symbols['indices'], symbols['stocks']
            self.cfg = core.Config(self.pth('NSE-OCA.ini'), self.indices, self.stocks, True)
            self.resume(session)
        else:
            self.load_symbols()

    def pth(self, name):
        return os.path.join(self.base, name)

    def on_pause(self):
        return True

    def on_keyboard(self, window, key, *args):
        if key == 27:  # Android back button
            if self.sm.current == 'main':
                self.close_main()
            else:
                self.quit_app()
            return True
        return False

    def quit_app(self):
        """Close the UI. If a background session is active it keeps running."""
        try:
            self.client.close()
        except Exception:
            pass
        App.get_running_app().stop()

    def stop_everything(self):
        """Stop the background service and close the app."""
        core.write_json(self.pth('control.json'), {'paused': True, 'quit': True})
        session = core.read_json(self.pth('session.json'), {})
        session['active'] = False
        core.write_json(self.pth('session.json'), session)
        if IS_ANDROID:
            try:
                from jnius import autoclass
                autoclass(SERVICE_CLASS).stop(android_activity())
            except Exception as err:
                print(err)
        self.quit_app()

    # ---- small utilities -------------------------------------------------
    @property
    def symbol(self):
        return self.cfg.index if self.cfg.option_mode == 'Index' else self.cfg.stock

    @property
    def repo(self):
        try:
            with open(os.path.join(APP_DIR, 'repo.txt')) as f:
                return f.read().strip() or core.ORIGINAL_REPO
        except OSError:
            return core.ORIGINAL_REPO

    def ui(self, fn, *args):
        Clock.schedule_once(lambda dt: fn(*args), 0)

    def info(self, title, message, cb=None):
        dialog(title, message, [("OK", cb)], auto_dismiss=True)

    def error(self, title, message, cb=None):
        dialog(title, message, [("OK", cb)])

    # ---- symbols / login screen -----------------------------------------
    def load_symbols(self):
        def work():
            try:
                indices, stocks = self.client.fetch_symbols()
                self.ui(self.symbols_ready, indices, stocks)
            except Exception as err:
                print(err, sys.exc_info()[0], "19/20")
                self.ui(self.symbols_failed)
        threading.Thread(target=work, daemon=True).start()

    def symbols_failed(self):
        dialog("Error", "Failed to fetch Symbols.\nCheck your internet connection.",
               [("Retry", self.load_symbols), ("Exit", self.quit_app)])

    def symbols_ready(self, indices, stocks):
        self.indices, self.stocks = indices, stocks
        core.write_json(self.pth('symbols.json'), {'indices': indices, 'stocks': stocks})
        self.cfg = core.Config(self.pth('NSE-OCA.ini'), indices, stocks, True)
        self.build_login()
        self.sm.current = 'login'
        self.fetch_dates()

    def build_login(self):
        if self.sm.has_screen('login'):
            self.sm.remove_widget(self.sm.get_screen('login'))
        screen = Screen(name='login')
        root = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(8))
        head = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        head.add_widget(Image(source=os.path.join(APP_DIR, 'nse_logo.png'), size_hint_x=None, width=dp(52)))
        head.add_widget(Label(text="NSE-Option-Chain-Analyzer", bold=True, halign='left',
                              text_size=(dp(240), None)))
        root.add_widget(head)
        grid = GridLayout(cols=2, size_hint_y=None, row_default_height=dp(48), row_force_default=True,
                          spacing=dp(8))
        grid.bind(minimum_height=grid.setter('height'))
        mode = self.cfg.option_mode
        grid.add_widget(Label(text="Mode:", halign='left'))
        self.mode_btn = Button(text=mode, on_release=lambda *a: self.change_option_mode())
        grid.add_widget(self.mode_btn)
        grid.add_widget(Label(text="Index:"))
        self.index_picker = PickerButton(self.indices, self.cfg.index, lambda v: self.fetch_dates(),
                                         searchable=True, disabled=(mode != 'Index'))
        grid.add_widget(self.index_picker)
        grid.add_widget(Label(text="Stock:"))
        self.stock_picker = PickerButton(self.stocks, self.cfg.stock, lambda v: self.fetch_dates(),
                                         searchable=True, disabled=(mode != 'Stock'))
        grid.add_widget(self.stock_picker)
        grid.add_widget(Label(text="Expiry Date:"))
        date_row = BoxLayout(spacing=dp(6))
        self.date_picker = PickerButton([""], "")
        date_row.add_widget(self.date_picker)
        date_row.add_widget(Button(text="Refresh", size_hint_x=None, width=dp(86),
                                   on_release=lambda *a: self.fetch_dates()))
        grid.add_widget(date_row)
        grid.add_widget(Label(text="Strike Price\n(eg. 14750):"))
        self.sp_entry = TextInput(multiline=False, input_filter='int', input_type='number')
        self.sp_entry.bind(on_text_validate=lambda *a: self.start())
        grid.add_widget(self.sp_entry)
        grid.add_widget(Label(text="Refresh Interval\n(in min):"))
        self.intervals_picker = PickerButton([str(i) for i in core.INTERVALS], str(int(self.cfg.seconds / 60)))
        grid.add_widget(self.intervals_picker)
        root.add_widget(grid)
        root.add_widget(Button(text="Start", size_hint_y=None, height=dp(56), bold=True,
                               on_release=lambda *a: self.start()))
        root.add_widget(Label())
        screen.add_widget(root)
        self.sm.add_widget(screen)

    def current_login_symbol(self):
        return self.index_picker.value if self.cfg.option_mode == 'Index' else self.stock_picker.value

    def change_option_mode(self):
        self.cfg.set('option_mode', 'Stock' if self.cfg.option_mode == 'Index' else 'Index')
        mode = self.cfg.option_mode
        self.mode_btn.text = mode
        self.index_picker.disabled = mode != 'Index'
        self.stock_picker.disabled = mode != 'Stock'
        self.fetch_dates()

    def fetch_dates(self):
        mode = self.cfg.option_mode
        symbol = self.current_login_symbol()
        self.cfg.set('index' if mode == 'Index' else 'stock', symbol)
        self._token += 1
        token = self._token

        def work():
            try:
                dates = self.client.fetch_expiry_dates(mode, symbol)
                self.ui(self.dates_ready, token, dates)
            except Exception as err:
                print(err, sys.exc_info()[0], "1/2")
                self.ui(self.dates_failed, token)
        threading.Thread(target=work, daemon=True).start()

    def dates_ready(self, token, dates):
        if token == self._token and dates:
            self.date_picker.set_values(dates)

    def dates_failed(self, token):
        if token != self._token:
            return
        self.date_picker.set_values([""], "")
        self.error("Error", "Error in fetching dates.\nPlease retry.")

    def start(self):
        seconds = int(self.intervals_picker.value) * 60
        self.cfg.set('seconds', seconds)
        expiry = self.date_picker.value
        if expiry == "":
            self.error("Error", "Incorrect Expiry Date.\nPlease enter correct Expiry Date.")
            return
        try:
            sp = int(self.sp_entry.text)
        except ValueError as err:
            print(err, sys.exc_info()[0], "7")
            self.error("Error", "Incorrect Strike Price.\nPlease enter correct Strike Price.")
            return
        self.cfg.set('index' if self.cfg.option_mode == 'Index' else 'stock', self.current_login_symbol())
        session = {'active': True, 'run_id': str(int(time.time())), 'mode': self.cfg.option_mode,
                   'symbol': self.symbol, 'expiry': expiry, 'sp': sp, 'seconds': seconds}
        core.write_json(self.pth('session.json'), session)
        core.write_json(self.pth('control.json'), {'paused': False, 'quit': False})
        try:
            os.remove(self.pth('state.json'))
        except OSError:
            pass
        self.main_win(session, fresh=True)

    def resume(self, session):
        self.main_win(session, fresh=False)

    # ---- main screen ------------------------------------------------------
    def main_win(self, session, fresh):
        self.session = session
        self.run_id = session['run_id']
        self.expiry_date, self.sp = session['expiry'], session['sp']
        self.units = core.units_for(session['mode'])
        self.output_columns, self.csv_headers = core.column_headers(session['mode'])
        self._seen_version = 0
        self._rows_shown = 0
        self._popup_seen = 0
        self._last_price = None
        self.table_rows = []
        self.trade = {}
        ctl = core.read_json(self.pth('control.json'), {})
        self.halted = bool(ctl.get('paused'))

        if self.sm.has_screen('main'):
            self.sm.remove_widget(self.sm.get_screen('main'))
        screen = Screen(name='main')
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(4))

        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.title_label = Label(text=f"{session['symbol']} - {self.expiry_date} - {self.sp}", bold=True,
                                 halign='left', valign='middle', shorten=True)
        self.title_label.bind(size=lambda *a: setattr(self.title_label, 'text_size', self.title_label.size))
        bar.add_widget(self.title_label)
        bar.add_widget(Button(text="Menu", size_hint_x=None, width=dp(90),
                              on_release=lambda *a: self.open_menu()))
        root.add_widget(bar)
        self.status = Label(text="Starting...", size_hint_y=None, height=dp(20), font_size=dp(11),
                            color=(0.7, 0.7, 0.7, 1), halign='left')
        self.status.bind(size=lambda *a: setattr(self.status, 'text_size', self.status.size))
        root.add_widget(self.status)
        self.trade_label = Label(text="Paper: no trade yet", size_hint_y=None, height=dp(22),
                                 font_size=dp(12), bold=True, color=hexc(core.GREEN), halign='left')
        self.trade_label.bind(size=lambda *a: setattr(self.trade_label, 'text_size', self.trade_label.size))
        root.add_widget(self.trade_label)

        hscroll = ScrollView(do_scroll_x=True, do_scroll_y=False, bar_width=dp(4))
        table = BoxLayout(orientation='vertical', size_hint=(None, 1), width=CELL_W * N_COLS, spacing=dp(1))
        header = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(1))
        for h in self.output_columns:
            header.add_widget(Cell(h, bold=True, font_size=dp(12)))
            header.children[0]._color.rgba = HEADER
        table.add_widget(header)
        self.rows_scroll = ScrollView(do_scroll_x=False, do_scroll_y=True, bar_width=dp(4))
        self.rows_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(1))
        self.rows_box.bind(minimum_height=self.rows_box.setter('height'))
        self.rows_scroll.add_widget(self.rows_box)
        table.add_widget(self.rows_scroll)
        hscroll.add_widget(table)
        root.add_widget(hscroll)

        panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(1))

        def row(*cells):
            r = BoxLayout(spacing=dp(1))
            for c in cells:
                r.add_widget(c)
            panel.add_widget(r)

        def lab(text):
            return Cell(text, bold=True, font_size=dp(12))

        self.v = {}

        def val(name):
            self.v[name] = Cell("", font_size=dp(13))
            return self.v[name]

        row(lab("Open Interest Upper Boundary"))
        row(lab("Strike Price 1:"), val('ub_sp1'), lab(f"OI ({self.units}):"), val('ub_oi1'))
        row(lab("Strike Price 2:"), val('ub_sp2'), lab(f"OI ({self.units}):"), val('ub_oi2'))
        row(lab("Open Interest Lower Boundary"))
        row(lab("Strike Price 1:"), val('lb_sp1'), lab(f"OI ({self.units}):"), val('lb_oi1'))
        row(lab("Strike Price 2:"), val('lb_sp2'), lab(f"OI ({self.units}):"), val('lb_oi2'))
        row(lab("Open Interest:"), val('oi'), lab("PCR:"), val('pcr'))
        row(lab("Call Exits:"), val('call_exits'), lab("Put Exits:"), val('put_exits'))
        row(lab("Call ITM:"), val('call_itm'), lab("Put ITM:"), val('put_itm'))
        panel.height = dp(34) * len(panel.children)
        root.add_widget(panel)

        screen.add_widget(root)
        self.sm.add_widget(screen)
        self.sm.current = 'main'

        if not self.halted:
            self.start_engine()
        if self._poll is not None:
            self._poll.cancel()
        self._poll = Clock.schedule_interval(self.poll_state, 1.5)
        if fresh:
            if self.cfg.update:
                self.check_for_updates()
            self.battery_hint()

    def battery_hint(self):
        flag = self.pth('battery_hint_done')
        if not IS_ANDROID or os.path.isfile(flag):
            return
        open(flag, 'w').write('1')
        dialog("Keep running in background",
               "So that Android does not stop the app, set Battery for this app to \"Unrestricted\" / "
               "\"Don't optimize\" and allow Notifications.\n\nOpen the battery settings now?",
               [("Open settings", open_battery_settings), ("Later", None)], height=.5)

    # ---- background engine ---------------------------------------------------
    def start_engine(self):
        if IS_ANDROID:
            try:
                from jnius import autoclass
                svc = autoclass(SERVICE_CLASS)
                act = android_activity()
                try:
                    svc.start(act, '', 'NSE Option Chain Analyzer', 'Fetching option chain in background', '')
                except Exception:
                    svc.start(act, '')
            except Exception:
                self.error("Service error", traceback.format_exc())
        else:  # desktop testing: run the engine in a thread
            import engine

            def work():
                class _N:
                    def notify(self, t, m):
                        print("[NOTIFY]", t, m)
                engine.Engine(self.base, self.export_dir, _N()).run()
            threading.Thread(target=work, daemon=True).start()

    def poll_state(self, dt):
        st = core.read_json(self.pth('state.json'), None)
        if not st or st.get('run_id') != self.run_id or st.get('version') == self._seen_version:
            return
        self._seen_version = st['version']
        self.status.text = st.get('status', '')
        self._last_price = st.get('last_price')
        for key, cell in self.v.items():
            if key in st.get('values', {}):
                cell.text = st['values'][key]
                cell.set_bg(st.get('label_bg', {}).get(key) if key in st.get('label_bg', {}) else None)
        rows, colors = st.get('rows', []), st.get('colors', [])
        at_bottom = self.rows_scroll.scroll_y <= 0.1
        for i in range(self._rows_shown, len(rows)):
            r = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(1))
            for text, bg in zip(rows[i], colors[i]):
                r.add_widget(Cell(str(text), bg=bg or None, font_size=dp(13)))
            self.rows_box.add_widget(r)
        if len(rows) > self._rows_shown and at_bottom:
            Clock.schedule_once(lambda dt: setattr(self.rows_scroll, 'scroll_y', 0), 0.1)
        self._rows_shown = len(rows)
        self.table_rows = rows
        self.trade = st.get('trade', {})
        self.trade_label.text = core.trade_summary(self.trade, self._last_price)[0]
        for p in st.get('popups', []):
            if p['id'] > self._popup_seen:
                self._popup_seen = p['id']
                cb = self.back_to_login if p.get('action') == 'login' else None
                self.error(p['title'], p['msg'], cb)
        if st.get('finished'):
            self.halted = True

    def back_to_login(self):
        if self._poll is not None:
            self._poll.cancel()
        session = core.read_json(self.pth('session.json'), {})
        session['active'] = False
        core.write_json(self.pth('session.json'), session)
        if not self.sm.has_screen('login'):
            self.build_login()
        self.sm.current = 'login'
        self.fetch_dates()

    # ---- menu --------------------------------------------------------------
    def open_menu(self):
        c = self.cfg
        onoff = lambda b: 'On' if b else 'Off'
        items = [
            ("Start" if self.halted else "Stop", self.change_state),
            ("Paper Trades", self.show_trades),
            ("Export Table to CSV", self.export),
            (f"Live Exporting to CSV: {onoff(c.live_export)}", self.toggle_live_export),
            (f"Dump Entire Option Chain to CSV: {onoff(c.save_oc)}", self.toggle_save_oc),
            (f"Notifications: {onoff(c.notifications)}", self.toggle_notifications),
            (f"Stop automatically at 3:30pm: {onoff(c.auto_stop)}", self.toggle_auto_stop),
            (f"Warn Late Server Updates: {onoff(c.warn_late_update)}", self.toggle_warn_late_update),
            ("Copy Table to Clipboard", self.copy_table),
            ("Save CSV Files to Downloads", self.save_all_to_downloads),
            ("Battery: allow background", open_battery_settings),
            (f"Auto Check for Updates: {onoff(c.update)}", self.toggle_updates),
            ("About", self.about),
            ("Quit (stops background too)", self.close_main),
        ]
        view = ModalView(size_hint=(.9, .85))
        outer = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(4))
        scroll = ScrollView()
        listing = GridLayout(cols=1, size_hint_y=None, spacing=dp(3))
        listing.bind(minimum_height=listing.setter('height'))
        for label, fn in items:
            b = Button(text=label, size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda btn, fn=fn: (view.dismiss(), Clock.schedule_once(lambda dt: fn(), 0.05)))
            listing.add_widget(b)
        scroll.add_widget(listing)
        outer.add_widget(scroll)
        view.add_widget(outer)
        view.open()

    def change_state(self):
        ctl = core.read_json(self.pth('control.json'), {})
        if not self.halted:
            self.halted = True
            core.write_json(self.pth('control.json'), {**ctl, 'paused': True})
            self.info("Stopped", "Retrieving new data has been stopped.")
        else:
            self.halted = False
            core.write_json(self.pth('control.json'), {**ctl, 'paused': False, 'quit': False})
            session = core.read_json(self.pth('session.json'), {})
            session['active'] = True
            core.write_json(self.pth('session.json'), session)
            self.start_engine()
            self.info("Started", "Retrieving new data has been started.")

    def show_trades(self):
        short, text = core.trade_summary(self.trade or {}, self._last_price)
        text += (f"\n\nEvery trade is also saved to NSE-OCA-PaperTrades-{self.session['symbol']}-"
                 f"{self.expiry_date}.csv")
        dialog("Paper Trades", text, [("OK", None)], height=.7, auto_dismiss=True)

    def toggle(self, key, on_title, on_msg, off_title, off_msg):
        new = not getattr(self.cfg, key)
        self.cfg.set(key, new)
        self.info(on_title if new else off_title, on_msg if new else off_msg)

    def toggle_live_export(self):
        self.toggle('live_export', "Live Exporting Enabled",
                    f"Data rows will be exported in real time to NSE-OCA-{self.session['symbol']}-{self.expiry_date}.csv.",
                    "Live Exporting Disabled", "Data rows will not be exported.")

    def toggle_save_oc(self):
        self.toggle('save_oc', "Dump Entire Option Chain Enabled",
                    f"Entire Option Chain data will be exported to NSE-OCA-{self.session['symbol']}-{self.expiry_date}-Full.csv.",
                    "Dump Entire Option Chain Disabled", "Entire Option Chain data will not be exported.")

    def toggle_notifications(self):
        self.toggle('notifications', "Notifications Enabled",
                    "You will get a popup notification with sound when the state of a label changes "
                    "and when a paper trade is taken.",
                    "Notifications Disabled", "You will not receive any Notifications.")

    def toggle_auto_stop(self):
        self.toggle('auto_stop', "Auto Stop Enabled", "Program will automatically stop at 3:30pm",
                    "Auto Stop Disabled", "Program will not automatically stop at 3:30pm")

    def toggle_warn_late_update(self):
        self.toggle('warn_late_update', "Warn Late Server Updates Enabled",
                    "Program will alert you if the server update time is 5 minutes or more.",
                    "Warn Late Server Updates Disabled", "Program will not alert you if the server updates late.")

    def toggle_updates(self):
        self.toggle('update', "Auto Checking for Updates Enabled", "Program will check for updates at start.",
                    "Auto Checking for Updates Disabled", "Program will not check for updates at start.")

    def copy_table(self):
        lines = ["\t".join(self.csv_headers)] + ["\t".join(str(x) for x in r) for r in self.table_rows]
        Clipboard.copy("\n".join(lines))
        self.info("Copied", "Table data copied to the clipboard.\nPaste it into any spreadsheet app.")

    def save_all_to_downloads(self):
        files = [f for f in os.listdir(self.export_dir) if f.startswith("NSE-OCA") and f.endswith((".csv", ".log"))]
        if not files:
            self.info("Nothing to save", "No CSV or log files have been created yet.")
            return
        done = sum(1 for f in files if copy_to_downloads(os.path.join(self.export_dir, f)))
        if IS_ANDROID:
            self.info("Saved", f"{done} of {len(files)} file(s) copied to Downloads/NSE-OCA.")
        else:
            self.info("Files location", f"Files are in:\n{self.export_dir}")

    def about(self):
        content = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        content.add_widget(Label(text="NSE-Option-Chain-Analyzer", bold=True, size_hint_y=None, height=dp(30)))
        content.add_widget(Label(text=f"Version: {core.VERSION} (Android)", size_hint_y=None, height=dp(28)))
        repo = self.repo
        links = [("Developer: Varun Shanbhag", "https://github.com/VarunS2002/"),
                 ("README", f"https://github.com/{repo}/blob/master/README.md"),
                 ("LICENSE", f"https://github.com/{repo}/blob/master/LICENSE"),
                 ("Releases", f"https://github.com/{repo}/releases/"),
                 ("Sources", f"https://github.com/{repo}/")]
        for text, url in links:
            content.add_widget(Button(text=text, size_hint_y=None, height=dp(40),
                                      on_release=lambda b, url=url: webbrowser.open(url)))
        content.add_widget(Button(text="Check for Updates", size_hint_y=None, height=dp(44),
                                  on_release=lambda b: self.check_for_updates(auto=False)))
        Popup(title="About", content=content, size_hint=(.88, .8)).open()

    # ---- updates -----------------------------------------------------------
    def check_for_updates(self, auto=True):
        def work():
            try:
                tag = core.latest_release_tag(self.repo)
                newer = core.is_newer(tag)
            except Exception as err:
                print(err, sys.exc_info()[0], "21")
                if not auto:
                    self.ui(self.error, "Error", "Failed to check for updates.")
                return
            if newer:
                msg = (f"You are running version: {core.VERSION}\nLatest version: {tag}\n"
                       f"Do you want to update now ?")
                self.ui(lambda: dialog("New Update Available", msg, [
                    ("Yes", lambda: webbrowser.open(f"https://github.com/{self.repo}/releases/latest")),
                    ("No", None)]))
            elif not auto:
                self.ui(self.info, "No Updates Available",
                        f"You are running the latest version.\nVersion: {core.VERSION}")
        threading.Thread(target=work, daemon=True).start()

    # ---- export --------------------------------------------------------------
    def export(self):
        sym = self.session['symbol']
        name = f"NSE-OCA-{sym}-{self.expiry_date}.csv"
        try:
            path = self.exporter.export_table(sym, self.expiry_date, self.csv_headers, self.table_rows)
            where = copy_to_downloads(path)
            msg = f"Data has been exported to {name}."
            msg += f"\nA copy was saved in {where}." if where else f"\nLocation: {self.export_dir}"
            self.info("Export Successful", msg)
        except PermissionError as err:
            print(err, sys.exc_info()[0], "12")
            self.error("Export Failed", f"Failed to access {name}.\nPermission Denied. Try closing any apps using it.")
        except Exception as err:
            print(err, sys.exc_info()[0], "8")
            self.error("Export Failed", "An error occurred while exporting the data.")

    def close_main(self):
        dialog("Close", "The app can keep fetching data and sending notifications in the background.",
               [("Minimize", minimize_app if IS_ANDROID else self.quit_app),
                ("Stop & Quit", self.stop_everything), ("Cancel", None)], height=.45)
