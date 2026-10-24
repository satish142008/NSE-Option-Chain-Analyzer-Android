"""
NSE-Option-Chain-Analyzer - Android (Kivy) port of Varun Shanbhag's desktop app v5.8 (GPLv3).
Same screens, menu options, colours, messages and refresh logic; UI rebuilt for touch.
"""
import datetime
import os
import sys
import threading
import time
import webbrowser

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.metrics import dp
from kivy.properties import ListProperty
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.popup import Popup
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex as hexc
from kivy.utils import platform

import nse_core as core

IS_ANDROID = platform == 'android'
APP_DIR = os.path.dirname(os.path.abspath(__file__))

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


def get_export_dir(default: str) -> str:
    if IS_ANDROID:
        try:
            d = android_activity().getExternalFilesDir(None)
            if d is not None:
                return d.getAbsolutePath()
        except Exception as err:
            print(err)
    return default


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


def keep_screen_on(flag: bool) -> None:
    if not IS_ANDROID:
        return
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass

        @run_on_ui_thread
        def _apply():
            lp = autoclass('android.view.WindowManager$LayoutParams')
            window = android_activity().getWindow()
            (window.addFlags if flag else window.clearFlags)(lp.FLAG_KEEP_SCREEN_ON)
        _apply()
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


class TableRow(RecycleDataViewBehavior, BoxLayout):
    texts = ListProperty([''] * N_COLS)
    bgs = ListProperty([''] * N_COLS)

    def __init__(self, **kw):
        super().__init__(orientation='horizontal', spacing=dp(1), **kw)
        self.cells = [Cell(font_size=dp(13)) for _ in range(N_COLS)]
        for c in self.cells:
            self.add_widget(c)

    def refresh_view_attrs(self, rv, index, data):
        self.texts = data['texts']
        self.bgs = data['bgs']
        for c, t, b in zip(self.cells, self.texts, self.bgs):
            c.text = t
            c.set_bg(b or None)
        return super().refresh_view_attrs(rv, index, data)


Factory.register('TableRow', cls=TableRow)


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
            shown = [v for v in self.values if q in v.lower()][:300]
            for v in shown:
                b = Button(text=v, size_hint_y=None, height=dp(44))
                b.bind(on_release=lambda btn, v=v: choose(v))
                listing.add_widget(b)

        search.bind(text=fill)
        fill()
        popup.open()


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
        Window.clearcolor = BG
        Window.softinput_mode = 'below_target'
        Window.bind(on_keyboard=self.on_keyboard)
        self.client = core.NseClient()
        self.cfg = None
        self.debuglog = None
        self.exporter = None
        self.stop = False
        self.first_run = True
        self._event = None
        self._busy = False
        self._token = 0
        self._error_open = False
        self.sm = ScreenManager(transition=NoTransition())
        loading = Screen(name='loading')
        box = BoxLayout(orientation='vertical', padding=dp(30), spacing=dp(12))
        box.add_widget(Image(source=os.path.join(APP_DIR, 'nse_logo.png'), size_hint_y=.5))
        box.add_widget(Label(text="NSE-Option-Chain-Analyzer\nLoading symbols...", halign='center'))
        loading.add_widget(box)
        self.sm.add_widget(loading)
        return self.sm

    def on_start(self):
        if IS_ANDROID:
            try:
                from android.permissions import request_permissions
                request_permissions(['android.permission.POST_NOTIFICATIONS',
                                     'android.permission.WRITE_EXTERNAL_STORAGE'])
            except Exception as err:
                print(err)
        self.data_dir = self.user_data_dir
        self.export_dir = get_export_dir(self.data_dir)
        os.makedirs(self.export_dir, exist_ok=True)
        self.exporter = core.CsvExporter(self.export_dir)
        self.load_symbols()

    def on_pause(self):
        return True

    def on_keyboard(self, window, key, *args):
        if key == 27:  # Android back button
            if self.sm.current == 'main':
                self.close_main()
            else:
                self.stop_app()
            return True
        return False

    def stop_app(self):
        try:
            self.client.close()
        except Exception:
            pass
        if self.cfg and self.cfg.logging:
            print('----------Quitting Program----------')
        keep_screen_on(False)
        self.stop_event()
        App.get_running_app().stop()

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

    def toast(self, title, message):
        p = Popup(title=title, content=Label(text=message), size_hint=(.8, .22))
        p.open()
        Clock.schedule_once(lambda dt: p.dismiss(), 4)

    def notify(self, title, message):
        try:
            from plyer import notification
            notification.notify(title=title, message=message, app_name="NSE-Option-Chain-Analyzer", timeout=4)
        except Exception as err:
            print(err)
            self.toast(title, message)

    def stop_event(self):
        if self._event is not None:
            self._event.cancel()
            self._event = None

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
        dialog("Error", "Failed to fetch Symbols.\nThe program will now exit.",
               [("Retry", self.load_symbols), ("Exit", self.stop_app)])

    def symbols_ready(self, indices, stocks):
        self.indices, self.stocks = indices, stocks
        self.cfg = core.Config(os.path.join(self.data_dir, 'NSE-OCA.ini'), indices, stocks, True)
        if self.cfg.logging:
            self.debuglog = core.DebugLog(os.path.join(self.export_dir, 'NSE-OCA.log'))
            self.debuglog.start()
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
        if self.cfg.option_mode == 'Index':
            self.cfg.set('option_mode', 'Stock')
        else:
            self.cfg.set('option_mode', 'Index')
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
        self.expiry_date = self.date_picker.value
        if self.expiry_date == "":
            self.error("Error", "Incorrect Expiry Date.\nPlease enter correct Expiry Date.")
            return
        try:
            self.sp = int(self.sp_entry.text)
        except ValueError as err:
            print(err, sys.exc_info()[0], "7")
            self.error("Error", "Incorrect Strike Price.\nPlease enter correct Strike Price.")
            return
        self.cfg.set('index' if self.cfg.option_mode == 'Index' else 'stock', self.current_login_symbol())
        if self.cfg.live_export:
            self.export_header_only()
        self.main_win()

    # ---- main screen ------------------------------------------------------
    def main_win(self):
        mode = self.cfg.option_mode
        self.units = core.units_for(mode)
        self.output_columns, self.csv_headers = core.column_headers(mode)
        self.first_run = True
        self.stop = False
        self.previous_date = self.previous_time = None
        self.prev = {}
        self.old_values = [None] * N_COLS
        self.table_rows = []
        self.expiry_date_used = self.expiry_date

        if self.sm.has_screen('main'):
            self.sm.remove_widget(self.sm.get_screen('main'))
        screen = Screen(name='main')
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(4))

        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.title_label = Label(text=f"{self.symbol} - {self.expiry_date} - {self.sp}", bold=True,
                                 halign='left', valign='middle', shorten=True)
        self.title_label.bind(size=lambda *a: setattr(self.title_label, 'text_size', self.title_label.size))
        bar.add_widget(self.title_label)
        bar.add_widget(Button(text="Menu", size_hint_x=None, width=dp(90),
                              on_release=lambda *a: self.open_menu()))
        root.add_widget(bar)

        hscroll = ScrollView(do_scroll_x=True, do_scroll_y=False, bar_width=dp(4))
        table = BoxLayout(orientation='vertical', size_hint=(None, 1), width=CELL_W * N_COLS, spacing=dp(1))
        header = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(1))
        for h in self.output_columns:
            header.add_widget(Cell(h, bold=True, font_size=dp(12)))
            header.children[0]._color.rgba = HEADER
        table.add_widget(header)
        self.rv = RecycleView(viewclass=TableRow, bar_width=dp(4))
        layout = RecycleBoxLayout(default_size=(None, dp(32)), default_size_hint=(1, None),
                                  size_hint_y=None, orientation='vertical', spacing=dp(1))
        layout.bind(minimum_height=layout.setter('height'))
        self.rv.add_widget(layout)
        self.rv.data = []
        table.add_widget(self.rv)
        hscroll.add_widget(table)
        root.add_widget(hscroll)

        panel = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(34) * 11, spacing=dp(1))

        def row(*cells):
            r = BoxLayout(spacing=dp(1))
            for c in cells:
                r.add_widget(c)
            panel.add_widget(r)

        def lab(text, bold=True):
            return Cell(text, bold=bold, font_size=dp(12))

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
        keep_screen_on(True)
        Clock.schedule_once(self.tick, 0.1)

    # ---- menu --------------------------------------------------------------
    def open_menu(self):
        c = self.cfg
        onoff = lambda b: 'On' if b else 'Off'
        items = [
            ("Start" if self.stop else "Stop", self.change_state),
            ("Export Table to CSV", self.export),
            (f"Live Exporting to CSV: {onoff(c.live_export)}", self.toggle_live_export),
            (f"Dump Entire Option Chain to CSV: {onoff(c.save_oc)}", self.toggle_save_oc),
            (f"Notifications: {onoff(c.notifications)}", self.toggle_notifications),
            (f"Stop automatically at 3:30pm: {onoff(c.auto_stop)}", self.toggle_auto_stop),
            (f"Warn Late Server Updates: {onoff(c.warn_late_update)}", self.toggle_warn_late_update),
            ("Copy Table to Clipboard", self.copy_table),
            ("Save CSV Files to Downloads", self.save_all_to_downloads),
            (f"Auto Check for Updates: {onoff(c.update)}", self.toggle_updates),
            (f"Debug Logging: {onoff(c.logging)}", self.log),
            ("About", self.about),
            ("Quit", self.close_main),
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
        if not self.stop:
            self.stop = True
            self.stop_event()
            self.info("Stopped", "Retrieving new data has been stopped.")
        else:
            self.stop = False
            self.info("Started", "Retrieving new data has been started.")
            self.tick()

    def toggle(self, key, on_title, on_msg, off_title, off_msg):
        new = not getattr(self.cfg, key)
        self.cfg.set(key, new)
        self.info(on_title if new else off_title, on_msg if new else off_msg)
        return new

    def toggle_live_export(self):
        self.toggle('live_export', "Live Exporting Enabled",
                    f"Data rows will be exported in real time to NSE-OCA-{self.symbol}-{self.expiry_date}.csv.",
                    "Live Exporting Disabled", "Data rows will not be exported.")
        self.export_header_only()

    def toggle_save_oc(self):
        self.toggle('save_oc', "Dump Entire Option Chain Enabled",
                    f"Entire Option Chain data will be exported to NSE-OCA-{self.symbol}-{self.expiry_date}-Full.csv.",
                    "Dump Entire Option Chain Disabled", "Entire Option Chain data will not be exported.")

    def toggle_notifications(self):
        self.toggle('notifications', "Notifications Enabled",
                    "You will receive Notifications when the state of a label changes.",
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

    def log(self):
        if not self.cfg.logging:
            path = os.path.join(self.export_dir, 'NSE-OCA.log')
            self.debuglog = core.DebugLog(path)
            self.debuglog.start()
            print(f"{'Android' if IS_ANDROID else sys.platform} .py version {core.VERSION}")
            self.cfg.set('logging', True)
            self.info("Debug Logging Enabled", f"Errors will be logged to NSE-OCA.log.\n{path}")
        else:
            if self.debuglog:
                self.debuglog.stop()
            self.cfg.set('logging', False)
            self.info("Debug Logging Disabled", "Errors will not be logged.")

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
                       f"Do you want to update now ?\n"
                       f"{'You can disable auto check for updates from the menu.' if auto and self.cfg.update else ''}")
                self.ui(lambda: dialog("New Update Available", msg, [
                    ("Yes", lambda: webbrowser.open(f"https://github.com/{self.repo}/releases/latest")),
                    ("No", None)]))
            elif not auto:
                self.ui(self.info, "No Updates Available",
                        f"You are running the latest version.\nVersion: {core.VERSION}")
        threading.Thread(target=work, daemon=True).start()

    # ---- export --------------------------------------------------------------
    def perm_msg(self, name):
        return f"Failed to access {name}.\nPermission Denied. Try closing any apps using it."

    def export_header_only(self):
        try:
            self.exporter.ensure_header(self.symbol, self.expiry_date, self.csv_headers_safe())
        except PermissionError as err:
            print(err, sys.exc_info()[0], "13")
            self.error("Export Failed", self.perm_msg(os.path.basename(self.exporter.path(self.symbol, self.expiry_date))))
        except Exception as err:
            print(err, sys.exc_info()[0], "9")

    def csv_headers_safe(self):
        if hasattr(self, 'csv_headers'):
            return self.csv_headers
        return core.column_headers(self.cfg.option_mode)[1]

    def export(self):
        name = f"NSE-OCA-{self.symbol}-{self.expiry_date}.csv"
        try:
            path = self.exporter.export_table(self.symbol, self.expiry_date, self.csv_headers, self.table_rows)
            where = copy_to_downloads(path)
            msg = f"Data has been exported to {name}."
            msg += f"\nA copy was saved in {where}." if where else f"\nLocation: {self.export_dir}"
            self.info("Export Successful", msg)
        except PermissionError as err:
            print(err, sys.exc_info()[0], "12")
            self.error("Export Failed", self.perm_msg(name))
        except Exception as err:
            print(err, sys.exc_info()[0], "8")
            self.error("Export Failed", "An error occurred while exporting the data.")

    def close_main(self):
        dialog("Quit", "All unsaved data will be lost.\nProceed to quit?",
               [("Yes", self.stop_app), ("No", None)])

    # ---- refresh loop (port of Nse.main) ----------------------------------------
    def schedule_next(self):
        self.stop_event()
        if not self.stop:
            self._event = Clock.schedule_once(self.tick, self.cfg.seconds)

    def tick(self, dt=None):
        self.stop_event()
        if self.stop or self._busy:
            return
        self._busy = True
        mode, symbol, expiry, first = self.cfg.option_mode, self.symbol, self.expiry_date, self.first_run

        def work():
            try:
                if first:
                    data = self.client.fetch_chain_first(mode, symbol, expiry)
                else:
                    data = self.client.fetch_chain_refresh(mode, symbol, expiry)
                self.ui(self.on_chain, data, None)
            except Exception as err:
                print(err, sys.exc_info()[0], "1")
                self.ui(self.on_chain, None, err)
        threading.Thread(target=work, daemon=True).start()

    def on_chain(self, json_data, err):
        self._busy = False
        if self.stop:
            return
        if err is not None:
            if not self._error_open:
                self._error_open = True
                self.error("Error", "Error in fetching dates.\nPlease retry.",
                           cb=lambda: setattr(self, '_error_open', False))
            return self.schedule_next()
        if json_data is None:
            return self.schedule_next()
        try:
            self.process(json_data)
        except Exception as e:
            print(e, sys.exc_info()[0], "process")
            self.schedule_next()

    def process(self, json_data):
        try:
            rows, timestamp, points = core.parse_chain(json_data, self.expiry_date)
        except core.InvalidExpiry:
            self.error("Error", "Invalid Expiry Date.\nPlease restart and enter a new Expiry Date.")
            self.stop = True
            self.info("Stopped", "Retrieving new data has been stopped.")
            return
        self.points = points
        str_time = timestamp.split(" ")[1]
        current_date = datetime.datetime.strptime(timestamp.split(" ")[0], '%d-%b-%Y').date()
        current_time = datetime.datetime.strptime(str_time, '%H:%M:%S').time()
        if self.first_run:
            self.previous_date, self.previous_time = current_date, current_time
        elif current_date > self.previous_date:
            self.previous_date, self.previous_time = current_date, current_time
        elif current_date == self.previous_date:
            if current_time > self.previous_time:
                diff = core.minutes_since(self.previous_time, current_time)
                if diff >= core.TIME_DIFFERENCE_FACTOR and self.cfg.warn_late_update:
                    msg = f"The data from the server was last updated about {int(diff)} minutes ago."
                    Clock.schedule_once(lambda dt: self.info("Late Update", msg), 2)
                self.previous_time = current_time
            else:
                return self.schedule_next()

        try:
            a = core.analyze(rows, self.sp, self.cfg.option_mode)
        except core.IncorrectStrike:
            self.stop = True
            self.error("Error", "Incorrect Strike Price.\nPlease enter correct Strike Price.",
                       cb=self.back_to_login)
            return
        if self.stop:
            return
        self.set_values(a, str_time)

        if self.cfg.save_oc:
            try:
                self.exporter.dump_full(self.symbol, self.expiry_date, rows)
            except PermissionError as err:
                print(err, sys.exc_info()[0], "11")
                self.error("Export Failed", self.perm_msg(f"NSE-OCA-{self.symbol}-{self.expiry_date}-Full.csv"))
            except Exception as err:
                print(err, sys.exc_info()[0], "16")

        if self.first_run:
            if self.cfg.update:
                self.check_for_updates()
            self.first_run = False
        today = datetime.datetime.strptime(time.strftime("%d-%b-%Y", time.localtime()), "%d-%b-%Y").date()
        if str_time == '15:30:00' and not self.stop and self.cfg.auto_stop and self.previous_date == today:
            self.stop = True
            self.info("Market Closed", "Retrieving new data has been stopped.")
            return
        self.schedule_next()

    def back_to_login(self):
        keep_screen_on(False)
        self.stop_event()
        self.sm.current = 'login'

    def changed(self, key, new, title, symbol_text=None):
        """Notification on change, mirroring the desktop `if first_run or old == new` blocks."""
        old = self.prev.get(key)
        if not self.first_run and old is not None and old != new and self.cfg.notifications:
            self.notify(f"{title} changed for {self.symbol}", f"Changed from {old} to {new}")
        self.prev[key] = new

    def set_values(self, a, str_time):
        v = self.v
        v['ub_oi1'].text, v['ub_sp1'].text = str(a.max_call_oi), str(a.max_call_oi_sp)
        v['ub_oi2'].text, v['ub_sp2'].text = str(a.max_call_oi_2), str(a.max_call_oi_sp_2)
        v['lb_oi1'].text, v['lb_sp1'].text = str(a.max_put_oi), str(a.max_put_oi_sp)
        v['lb_oi2'].text, v['lb_sp2'].text = str(a.max_put_oi_2), str(a.max_put_oi_sp_2)
        self.changed('ub1', a.max_call_oi_sp, "Upper Boundary Strike Price")
        self.changed('ub2', a.max_call_oi_sp_2, "Upper Boundary Strike Price 2")
        self.changed('lb1', a.max_put_oi_sp, "Lower Boundary Strike Price")
        self.changed('lb2', a.max_put_oi_sp_2, "Lower Boundary Strike Price 2")

        labels = core.compute_labels(a)
        titles = {'oi': "Open Interest", 'call_itm': "Call ITM", 'put_itm': "Put ITM",
                  'call_exits': "Call Exits", 'put_exits': "Put Exits"}
        for key, (text, bg) in labels.items():
            v[key].text = str(text)
            v[key].set_bg(bg)
            if key in titles:
                self.changed(key, text, titles[key])

        values = [str_time, self.points, a.call_sum, a.put_sum, a.difference, a.call_boundary,
                  a.put_boundary, a.call_itm, a.put_itm]
        colors = [''] * N_COLS
        for col in range(1, N_COLS):
            colors[col] = core.trend_color(col, values[col], self.old_values[col]) or ''
            self.old_values[col] = values[col]
        self.table_rows.append(values)
        if self.cfg.live_export:
            try:
                self.exporter.export_row(self.symbol, self.expiry_date, values)
            except PermissionError as err:
                print(err, sys.exc_info()[0], "14")
                self.error("Export Failed", self.perm_msg(f"NSE-OCA-{self.symbol}-{self.expiry_date}.csv"))
            except Exception as err:
                print(err, sys.exc_info()[0], "15")

        at_bottom = self.rv.scroll_y <= 0.1
        self.rv.data = self.rv.data + [{'texts': [str(x) for x in values], 'bgs': colors}]
        if at_bottom:
            Clock.schedule_once(lambda dt: setattr(self.rv, 'scroll_y', 0), 0.05)


if __name__ == '__main__':
    NseApp().run()
