"""Launcher: shows any startup error on screen instead of silently closing the app."""
import traceback

try:
    from nse_app import NseApp
    _ERR = None
except Exception:
    NseApp = None
    _ERR = traceback.format_exc()

if NseApp is None:
    from kivy.app import App
    from kivy.metrics import dp
    from kivy.uix.label import Label
    from kivy.uix.scrollview import ScrollView

    class ErrApp(App):
        def build(self):
            sv = ScrollView()
            lb = Label(text="App error - please send a screenshot:\n\n" + _ERR, size_hint_y=None,
                       font_size=dp(12), halign='left', valign='top')
            lb.bind(width=lambda *a: setattr(lb, 'text_size', (lb.width, None)),
                    texture_size=lambda *a: setattr(lb, 'height', lb.texture_size[1]))
            sv.add_widget(lb)
            return sv

    ErrApp().run()
elif __name__ == '__main__':
    NseApp().run()
