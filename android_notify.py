"""Heads-up Android notifications with the custom tone (runs inside the background service)."""
import os

from jnius import autoclass


class AndroidNotifier:
    def __init__(self, base_dir: str):
        self.base = base_dir
        self.ctx = autoclass('org.kivy.android.PythonService').mService
        self.sdk = autoclass('android.os.Build$VERSION').SDK_INT
        self.nm = self.ctx.getSystemService(autoclass('android.content.Context').NOTIFICATION_SERVICE)
        self.counter = 1000
        self.uri = None
        try:
            self.uri = self._sound_uri()
        except Exception as err:
            self._err(f"sound: {err}")
        self.channel_id = 'nse_alerts_tone' if self.uri is not None else 'nse_alerts_default'
        if self.sdk >= 26:
            self._create_channel()

    def _err(self, msg):
        print(msg)

    def _sound_uri(self):
        """Copy the bundled mp3 into the device's Notifications folder (MediaStore) and return its Uri."""
        Uri = autoclass('android.net.Uri')
        resolver = self.ctx.getContentResolver()
        uri_file = os.path.join(self.base, 'sound_uri.txt')
        if os.path.isfile(uri_file):
            try:
                uri = Uri.parse(open(uri_file).read().strip())
                resolver.openInputStream(uri).close()
                return uri
            except Exception:
                pass
        mp3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'notification_tone.mp3')
        data = open(mp3, 'rb').read()
        if self.sdk >= 29:
            ContentValues = autoclass('android.content.ContentValues')
            Cols = autoclass('android.provider.MediaStore$MediaColumns')
            Audio = autoclass('android.provider.MediaStore$Audio$Media')
            values = ContentValues()
            values.put(Cols.DISPLAY_NAME, 'nse_oca_tone.mp3')
            values.put(Cols.MIME_TYPE, 'audio/mpeg')
            values.put(Cols.RELATIVE_PATH, 'Notifications')
            uri = resolver.insert(Audio.EXTERNAL_CONTENT_URI, values)
            out = resolver.openOutputStream(uri)
            for i in range(0, len(data), 65536):
                out.write(bytearray(data[i:i + 65536]))
            out.close()
        else:
            File = autoclass('java.io.File')
            Env = autoclass('android.os.Environment')
            folder = Env.getExternalStoragePublicDirectory('Notifications')
            folder.mkdirs()
            path = os.path.join(folder.getAbsolutePath(), 'nse_oca_tone.mp3')
            with open(path, 'wb') as f:
                f.write(data)
            uri = Uri.fromFile(File(path))
        with open(uri_file, 'w') as f:
            f.write(uri.toString())
        return uri

    def _create_channel(self):
        NotificationChannel = autoclass('android.app.NotificationChannel')
        NM = autoclass('android.app.NotificationManager')
        ch = NotificationChannel(self.channel_id, 'NSE Alerts', NM.IMPORTANCE_HIGH)  # HIGH = heads-up popup
        ch.setDescription('Option chain signal changes and paper trades')
        ch.enableVibration(True)
        ch.setLockscreenVisibility(1)
        if self.uri is not None:
            AA = autoclass('android.media.AudioAttributes')
            AAB = autoclass('android.media.AudioAttributes$Builder')
            attrs = AAB().setUsage(AA.USAGE_NOTIFICATION).setContentType(AA.CONTENT_TYPE_SONIFICATION).build()
            ch.setSound(self.uri, attrs)
        self.nm.createNotificationChannel(ch)

    def notify(self, title: str, message: str) -> None:
        Builder = autoclass('android.app.Notification$Builder')
        Notification = autoclass('android.app.Notification')
        PendingIntent = autoclass('android.app.PendingIntent')
        intent = self.ctx.getPackageManager().getLaunchIntentForPackage(self.ctx.getPackageName())
        pi = PendingIntent.getActivity(self.ctx, 0, intent,
                                       PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT)
        if self.sdk >= 26:
            b = Builder(self.ctx, self.channel_id)
        else:
            b = Builder(self.ctx)
            if self.uri is not None:
                b.setSound(self.uri)
            b.setDefaults(Notification.DEFAULT_VIBRATE)
        b.setSmallIcon(self.ctx.getApplicationInfo().icon)
        b.setContentTitle(title)
        b.setContentText(message.split("\n")[0])
        b.setStyle(autoclass('android.app.Notification$BigTextStyle')().bigText(message))
        b.setPriority(Notification.PRIORITY_HIGH)
        b.setAutoCancel(True)
        b.setContentIntent(pi)
        self.counter += 1
        self.nm.notify(self.counter, b.build())
