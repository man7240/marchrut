import os
import sys
import json
import math
import time
import re
import sqlite3
import struct
import zlib
import traceback
from datetime import datetime
print('NEARBY_FINDER_MAIN_START build=23', flush=True)
BUILD = 23
OSM_RADIUS = 1500
OSM_CACHE_SEC = 300
WALK_SPEED_MPM = 80.0
BASE_RADIUS_M = 3000
UI_FILTER_DELAY = 0.30   # замедленная реакция фильтров (сек)
UI_TAP_DELAY = 0.35      # замедленная реакция тапа по карте (сек)
CHAIN_HOURS = {'апрель': ('08:00', '22:00')}

def _tb_text():
    return ''.join(traceback.format_exception(*sys.exc_info()))

def _run_error_app(title, text):
    try:
        from kivy.app import App
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.label import Label
        from kivy.uix.boxlayout import BoxLayout
        from kivy.core.window import Window
        from kivy.metrics import dp
        Window.clearcolor = (0.06, 0.07, 0.10, 1)
        class _ErrApp(App):
            def build(self):
                root = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
                ttl = Label(text='[b]%s (build %d)[/b]' % (title, BUILD),
                            markup=True, size_hint_y=None, height=dp(40),
                            halign='left', valign='middle',
                            color=(1, 0.85, 0.3, 1), font_size='15sp')
                ttl.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
                sv = ScrollView(do_scroll_y=True, bar_width=dp(4),
                                bar_color=(0.3, 0.6, 1, 0.6))
                body = Label(text=text, size_hint_y=None, halign='left',
                             valign='top', markup=False, font_size='12sp',
                             color=(0.9, 0.9, 0.9, 1))
                body.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
                body.bind(texture_size=lambda i, v: setattr(i, 'height', v[1] + dp(12)))
                sv.add_widget(body)
                root.add_widget(ttl)
                root.add_widget(sv)
                return root
        _ErrApp().run()
    except Exception:
        print('FATAL STARTUP ERROR build=%d:\n%s\n%s' % (BUILD, title, text), flush=True)

def _save_crash(text):
    try:
        from kivy.app import App
        app = App.get_running_app()
        d = app.user_data_dir if app else '.'
    except Exception:
        d = '.'
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'crash.log'), 'a', encoding='utf-8') as f:
            f.write('--- crash build=%d ---\n%s\n' % (BUILD, text))
    except Exception:
        pass

def _write_png(path, w, h, px):
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            r, g, b, a = px(x, y)
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    raw = b''.join(rows)
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', zlib.compress(raw, 9)))
        f.write(chunk(b'IEND', b''))

def _px_line(x, y):
    d = math.hypot(x - 6.5, y - 6.5)
    if d <= 4.0:
        return (77, 166, 255, 255)
    if d <= 6.0:
        return (255, 255, 255, 230)
    return (0, 0, 0, 0)

def _px_user(x, y):
    d = math.hypot(x - 10.5, y - 10.5)
    if d <= 6.5:
        return (33, 120, 255, 255)
    if d <= 9.0:
        return (255, 255, 255, 240)
    return (0, 0, 0, 0)

try:
    from urllib.parse import quote
    from kivy.app import App
    from kivy.core.window import Window
    from kivy.uix.screenmanager import ScreenManager, Screen
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.gridlayout import GridLayout
    from kivy.uix.floatlayout import FloatLayout
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.label import Label
    from kivy.uix.button import Button
    from kivy.uix.textinput import TextInput
    from kivy.uix.widget import Widget
    from kivy.uix.togglebutton import ToggleButton
    from kivy.uix.behaviors import ButtonBehavior
    from kivy.clock import Clock
    from kivy.utils import platform
    from kivy.logger import Logger
    from kivy.metrics import dp
    from kivy.graphics import Color, Rectangle, RoundedRectangle
    Window.clearcolor = (0.06, 0.07, 0.10, 1)

    def _excepthook(exc_type, exc_value, exc_tb):
        try:
            txt = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
            _save_crash(txt)
            try:
                app = App.get_running_app()
                if app is not None and hasattr(app, 'show_error'):
                    app.show_error('Неперехваченная ошибка', txt)
            except Exception:
                pass
        except Exception:
            pass
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass
    sys.excepthook = _excepthook

    MAPVIEW_OK = False
    MapView = None
    MapMarker = None
    try:
        from kivy_garden.mapview import MapView, MapMarker, MapLayer
        MAPVIEW_OK = True
    except Exception as e:
        Logger.error('MapView import error: %s' % e)

    # ----------------------- последняя позиция -----------------------
    def _last_fix_path():
        try:
            app = App.get_running_app()
            d = app.user_data_dir if app else '.'
            return os.path.join(d, 'last_fix.json')
        except Exception:
            return 'last_fix.json'

    def _load_last_fix():
        try:
            p = _last_fix_path()
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if 'lat' in d and 'lon' in d:
                    return d
        except Exception:
            pass
        return None

    def _save_last_fix(fix):
        try:
            p = _last_fix_path()
            os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'lat': fix['lat'], 'lon': fix['lon'],
                           'accuracy': fix.get('accuracy', 0)}, f)
        except Exception:
            pass

    # ----------------------- кэш доопределённых адресов -----------------------
    def _addr_cache_path():
        try:
            app = App.get_running_app()
            d = app.user_data_dir if app else '.'
            return os.path.join(d, 'addr_cache.json')
        except Exception:
            return 'addr_cache.json'

    def _addr_cache_load():
        try:
            p = _addr_cache_path()
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _addr_cache_save(cache):
        try:
            with open(_addr_cache_path(), 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _addr_key(rec):
        return '%s_%.5f_%.5f' % (rec.get('id'), rec.get('lat'), rec.get('lon'))

    def _apply_chain_defaults(rec):
        try:
            if rec.get('open_24') or rec.get('hours_known', 1):
                return rec
            nm = (rec.get('name') or '').lower()
            for key, (ho, hc) in CHAIN_HOURS.items():
                if key in nm:
                    rec['hours_open'] = ho
                    rec['hours_close'] = hc
                    rec['hours_known'] = 1
                    break
        except Exception:
            pass
        return rec

    # ----------------------- утилиты вёрстки -----------------------
    def _auto_label(text='', font_size='13sp', **kw):
        lbl = Label(text=text, size_hint_y=None, halign='left', valign='top',
                    font_size=font_size, **kw)
        lbl.bind(width=lambda inst, w: setattr(inst, 'text_size', (max(10, w), None)))
        lbl.bind(texture_size=lambda inst, v: setattr(inst, 'height', v[1] + dp(6)))
        return lbl

    def _wrap(text='', font_size='13sp', color=(1, 1, 1, 1),
              markup=False, bold=False, shorten=False):
        lbl = Label(text=text, size_hint_y=None, halign='left', valign='middle',
                    font_size=font_size, color=color, markup=markup,
                    bold=bold, shorten=shorten)
        lbl.bind(width=lambda i, w: setattr(i, 'text_size', (max(10, w), None)))
        lbl.bind(texture_size=lambda i, v: setattr(i, 'height', max(v[1] + dp(4), dp(18))))
        return lbl

    def _btn(text, bg, height=None, font_size='15sp'):
        if height is None:
            height = dp(78)
        b = Button(text=text, size_hint_y=None, height=height,
                   font_size=font_size, bold=True, color=(1, 1, 1, 1),
                   background_color=bg, halign='center', valign='middle',
                   shorten=False)
        b.bind(size=lambda inst, v: setattr(
            inst, 'text_size', (max(10, v[0] - dp(10)), None)))
        return b

    def _field(hint='', multiline=False, height=None):
        t = TextInput(hint_text=hint, multiline=multiline, size_hint_y=None,
                      height=height or dp(44), font_size='14sp',
                      background_color=(0.16, 0.18, 0.23, 1),
                      foreground_color=(0.95, 0.96, 1, 1),
                      hint_text_color=(0.55, 0.58, 0.66, 1),
                      padding=[dp(10), dp(8)], cursor_color=(0.3, 0.7, 1, 1))
        return t

    COLOR_PHARMACY = (0.10, 0.72, 0.50, 1)
    COLOR_ATM = (0.95, 0.66, 0.13, 1)
    COLOR_POINT = (0.62, 0.64, 0.70, 1)
    COLOR_OPEN = (0.20, 0.80, 0.40, 1)
    COLOR_CLOSED = (0.85, 0.30, 0.30, 1)
    COLOR_UNKNOWN = (0.45, 0.48, 0.56, 1)

    def type_color(t):
        return COLOR_PHARMACY if t == 'pharmacy' else (COLOR_ATM if t == 'atm' else COLOR_POINT)

    def type_label(t):
        return 'Аптека' if t == 'pharmacy' else ('Банкомат' if t == 'atm' else 'Точка')

    def _tag(text, bg, font_size='12sp'):
        lbl = Label(text='  %s  ' % text, size_hint=(None, None), height=dp(24),
                    font_size=font_size, bold=True, color=(1, 1, 1, 1),
                    halign='center', valign='middle')
        lbl.texture_update()
        lbl.width = lbl.texture_size[0] + dp(8)
        lbl.bind(texture_size=lambda i, v: setattr(i, 'width', v[0] + dp(8)))
        with lbl.canvas.before:
            Color(*bg)
            rect = RoundedRectangle(size=lbl.size, pos=lbl.pos, radius=[dp(5)])
        lbl.bind(pos=lambda i, v: setattr(rect, 'pos', v),
                 size=lambda i, v: setattr(rect, 'size', v))
        return lbl

    def request_android_permissions():
        if platform != 'android':
            return False
        try:
            from android.permissions import request_permissions, Permission
            request_permissions([
                Permission.ACCESS_FINE_LOCATION,
                Permission.ACCESS_COARSE_LOCATION,
            ])
            return True
        except Exception as e:
            Logger.error('Android permissions error: %s' % e)
            return False

    def make_map(zoom=15, center=None):
        if not MAPVIEW_OK:
            return None
        try:
            if center is None:
                fix = _load_last_fix()
                center = (fix['lat'], fix['lon']) if fix else (0.0, 0.0)
            mv = MapView(zoom=zoom, lat=center[0], lon=center[1])
            try:
                app = App.get_running_app()
                base = app.user_data_dir if app else '.'
                mv.cache_dir = os.path.join(base, 'tiles')
                os.makedirs(mv.cache_dir, exist_ok=True)
            except Exception:
                pass
            return mv
        except Exception as e:
            Logger.error('MapView create error: %s' % e)
            return None

    def _map_add_marker(mv, mk):
        try:
            if hasattr(mv, 'add_marker'):
                mv.add_marker(mk)
            else:
                mv.add_widget(mk)
        except Exception:
            try:
                mv.add_widget(mk)
            except Exception:
                pass

    def _map_remove_marker(mv, mk):
        try:
            if hasattr(mv, 'remove_marker'):
                mv.remove_marker(mk)
            else:
                mv.remove_widget(mk)
        except Exception:
            try:
                mv.remove_widget(mk)
            except Exception:
                pass

    # ----------------------- геолокация -----------------------
    class LocationService:
        def __init__(self):
            self._lm = None
            self._listener = None
            self._timer = None
            self._best_acc = 1e9
            self.on_fix = None
            self.on_status = None
            self.last = None
            self._continuous = False
        def _ctx(self):
            from jnius import autoclass
            return autoclass('org.kivy.android.PythonActivity').mActivity
        def has_permission(self):
            if platform != 'android':
                return True
            try:
                from jnius import autoclass
                PM = autoclass('android.content.pm.PackageManager')
                P = autoclass('android.Manifest$permission')
                G = PM.PERMISSION_GRANTED
                c = self._ctx()
                return (c.checkSelfPermission(P.ACCESS_FINE_LOCATION) == G or
                        c.checkSelfPermission(P.ACCESS_COARSE_LOCATION) == G)
            except Exception:
                return True
        def request(self, on_fix, on_status=None, timeout=18.0, continuous=False):
            self.stop()
            self.on_fix = on_fix
            self.on_status = on_status
            self._continuous = bool(continuous)
            if platform != 'android':
                if on_status:
                    on_status('Не Android: позиция недоступна')
                return
            if not self.has_permission():
                request_android_permissions()
                if on_status:
                    on_status('Выдайте разрешение «Местоположение» в настройках')
                return
            try:
                from jnius import autoclass, PythonJavaClass, java_method
                Context = autoclass('android.content.Context')
                Looper = autoclass('android.os.Looper')
                ctx = self._ctx()
                self._lm = ctx.getSystemService(Context.LOCATION_SERVICE)
                outer = self
                class _L(PythonJavaClass):
                    __javainterfaces__ = ['android/location/LocationListener']
                    @java_method('(Landroid/location/Location;)V')
                    def onLocationChanged(self, loc):
                        try:
                            fix = {'lat': float(loc.getLatitude()),
                                   'lon': float(loc.getLongitude()),
                                   'accuracy': float(loc.getAccuracy())}
                            outer.last = fix
                            if fix['accuracy'] < outer._best_acc:
                                outer._best_acc = fix['accuracy']
                            if outer.on_fix:
                                Clock.schedule_once(lambda dt, f=fix: outer.on_fix(f), 0)
                        except Exception as e:
                            Logger.error('GPS onLocationChanged error: %s' % e)
                    @java_method('(Ljava/lang/String;)V')
                    def onProviderEnabled(self, p):
                        pass
                    @java_method('(Ljava/lang/String;)V')
                    def onProviderDisabled(self, p):
                        pass
                    @java_method('(Ljava/lang/String;ILandroid/os/Bundle;)V')
                    def onStatusChanged(self, p, s, e):
                        pass
                self._listener = _L()
                ml = Looper.getMainLooper()
                for prov in ('gps', 'network'):
                    try:
                        if self._lm.isProviderEnabled(prov):
                            self._lm.requestLocationUpdates(
                                prov, 1000, 1.0, self._listener, ml)
                    except Exception as e:
                        Logger.error('rlu %s: %s' % (prov, e))
                self._best_acc = 1e9
                if on_status:
                    on_status('Определяем местоположение…')
                self._timer = Clock.schedule_interval(self._poll, 1.0 if continuous else 1.5)
                if not continuous:
                    Clock.schedule_once(lambda dt: self.stop(), timeout)
            except Exception as e:
                Logger.error('location init error: %s' % e)
                if on_status:
                    on_status('Ошибка инициализации GPS: %s' % e)
                return
        def _poll(self, dt):
            if self._lm is None:
                return
            best = None
            for prov in ('gps', 'network'):
                try:
                    loc = self._lm.getLastKnownLocation(prov)
                except Exception:
                    loc = None
                if loc is None:
                    continue
                if best is None or loc.getTime() > best.getTime():
                    best = loc
            if best is None:
                return
            acc = float(best.getAccuracy())
            fix = {'lat': float(best.getLatitude()),
                   'lon': float(best.getLongitude()), 'accuracy': acc}
            if acc < self._best_acc:
                self._best_acc = acc
            if self._continuous or acc <= self._best_acc + 1e-6:
                self.last = fix
                if self.on_fix:
                    self.on_fix(fix)
            if not self._continuous and acc <= 35.0:
                self.stop()
        def stop(self):
            if self._timer is not None:
                try:
                    Clock.unschedule(self._timer)
                except Exception:
                    pass
                self._timer = None
            try:
                if self._lm is not None and self._listener is not None:
                    self._lm.removeUpdates(self._listener)
            except Exception:
                pass

    # ----------------------- расчёты -----------------------
    class RouteCalculator:
        @staticmethod
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000.0
            p1 = math.radians(float(lat1)); p2 = math.radians(float(lat2))
            dp_ = math.radians(float(lat2) - float(lat1))
            dl = math.radians(float(lon2) - float(lon1))
            a = (math.sin(dp_ / 2.0) ** 2 +
                 math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
            return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        @staticmethod
        def distance_to(user, place):
            if not user:
                return None
            return RouteCalculator.haversine(user['lat'], user['lon'],
                                             place['lat'], place['lon'])

    def fmt_dist(m):
        if m is None:
            return '—'
        if m < 1000:
            return '%d м' % int(round(m))
        return '%.2f км' % (m / 1000.0)

    def _to_min(hhmm):
        try:
            h, m = hhmm.split(':')
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    def is_open_now(rec, now_min=None):
        try:
            if rec.get('open_24'):
                return True
            if not rec.get('hours_known', 1):
                return None
            if now_min is None:
                t = time.localtime()
                now_min = t.tm_hour * 60 + t.tm_min
            return _to_min(rec.get('hours_open', '00:00')) <= now_min < _to_min(rec.get('hours_close', '00:00'))
        except Exception:
            return None

    def filter_places(rows, type_filter, mode_filter, user):
        t = time.localtime()
        now_min = t.tm_hour * 60 + t.tm_min
        out = []
        for r in rows:
            try:
                if type_filter != 'all' and r.get('type') != type_filter:
                    continue
                if mode_filter == 'open':
                    v = is_open_now(r, now_min)
                    if v is False:
                        continue
                if mode_filter == '24' and not r.get('open_24'):
                    continue
                out.append(r)
            except Exception:
                continue
        if user:
            out.sort(key=lambda r: (RouteCalculator.distance_to(user, r) or 1e18))
        else:
            out.sort(key=lambda r: r.get('name', ''))
        return out

    # ----------------------- живая база -----------------------
    class Database:
        def __init__(self, db_path='places.db'):
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS places (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, type TEXT NOT NULL,
                    lat REAL NOT NULL, lon REAL NOT NULL,
                    address TEXT, hours_open TEXT, hours_close TEXT,
                    open_24 INTEGER NOT NULL DEFAULT 0, phone TEXT
                )
            ''')
            self.conn.commit()
            self._migrate()
        def _migrate(self):
            try:
                cols = {r[1] for r in self.conn.execute('PRAGMA table_info(places)')}
                if 'source' not in cols:
                    self.conn.execute("ALTER TABLE places ADD COLUMN source TEXT DEFAULT 'user'")
                if 'hours_known' not in cols:
                    self.conn.execute('ALTER TABLE places ADD COLUMN hours_known INTEGER DEFAULT 1')
                if 'geo_fixed' not in cols:
                    self.conn.execute('ALTER TABLE places ADD COLUMN geo_fixed INTEGER DEFAULT 0')
                self.conn.execute("UPDATE places SET source='user' WHERE source IS NULL")
                self.conn.execute("DELETE FROM places WHERE source='seed'")
                self.conn.commit()
            except Exception as e:
                Logger.error('db migrate error: %s' % e)
        def all_places(self):
            rows = self.conn.execute('SELECT * FROM places').fetchall()
            return [dict(r) for r in rows]
        def add_place(self, name, typ, lat, lon, address, ho, hc, o24, phone, hours_known):
            self.conn.execute('''
                INSERT INTO places
                (name, type, lat, lon, address, hours_open, hours_close, open_24, phone, source, hours_known, geo_fixed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?, 1)
            ''', (name, typ, lat, lon, address, ho, hc, o24, phone, hours_known))
            self.conn.commit()
        def replace_osm(self, rows):
            try:
                self.conn.execute("DELETE FROM places WHERE source='osm'")
                self.conn.executemany('''
                    INSERT INTO places
                    (name, type, lat, lon, address, hours_open, hours_close, open_24, phone, source, hours_known, geo_fixed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'osm', ?, 1)
                ''', [(r['name'], r['type'], r['lat'], r['lon'], r['address'],
                       r['hours_open'], r['hours_close'], r['open_24'],
                       r['phone'], r['hours_known']) for r in rows])
                self.conn.commit()
            except Exception as e:
                Logger.error('replace_osm error: %s' % e)
        def close(self):
            try:
                self.conn.close()
            except Exception:
                pass

    # ----------------------- Overpass -----------------------
    def _parse_oh(oh):
        if not oh:
            return ('00:00', '00:00', 0, 0)
        s = oh.lower()
        if '24/7' in s or '24/7' in oh:
            return ('00:00', '00:00', 1, 1)
        m = re.search(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', oh)
        if m:
            return (m.group(1), m.group(2), 0, 1)
        return ('00:00', '00:00', 0, 0)

    def _norm_osm(el):
        tags = el.get('tags', {}) or {}
        amenity = tags.get('amenity', '')
        typ = 'pharmacy' if amenity == 'pharmacy' else 'atm'
        name = tags.get('name') or tags.get('brand') or type_label(typ)
        street = (tags.get('addr:street', '') or '').strip()
        house = (tags.get('addr:housenumber', '') or '').strip()
        addr = (street + (' ' + house if house else '')).strip() or tags.get('addr:full', '') or ''
        phone = tags.get('phone') or tags.get('contact:phone') or ''
        ho, hc, o24, known = _parse_oh(tags.get('opening_hours', ''))
        return {'name': name, 'type': typ,
                'lat': float(el['lat']), 'lon': float(el['lon']),
                'address': addr, 'hours_open': ho, 'hours_close': hc,
                'open_24': o24, 'phone': phone, 'hours_known': known}

    OSM_MIRRORS = ['https://overpass-api.de/api/interpreter',
                   'https://overpass.kumi.systems/api/interpreter',
                   'https://overpass.private.coffee/api/interpreter']

    class OsmLoader:
        def __init__(self, db):
            self.db = db
            self._last_at = 0
            self._last_key = None
            self.busy = False
            self.on_done = None
        def load(self, lat, lon, on_done=None, force=False):
            self.on_done = on_done
            key = (round(lat, 3), round(lon, 3))
            now = time.time()
            if not force and self.busy:
                return
            if not force and self._last_key == key and (now - self._last_at) < OSM_CACHE_SEC:
                if on_done:
                    on_done(True)
                return
            self.busy = True
            q = ('[out:json][timeout:15];(node["amenity"~"^(pharmacy|atm)$"]'
                 '(around:%d,%f,%f););out body;' % (OSM_RADIUS, lat, lon))
            body = 'data=' + quote(q)
            def try_mirror(idx):
                if idx >= len(OSM_MIRRORS):
                    self.busy = False
                    Logger.error('OSM: все зеркала недоступны')
                    if on_done:
                        on_done(False)
                    return
                url = OSM_MIRRORS[idx] + '?' + body
                def _ok(req, result):
                    try:
                        data = result if isinstance(result, dict) else json.loads(result)
                        els = data.get('elements', []) or []
                        rows = []
                        for el in els:
                            try:
                                if 'lat' in el and 'lon' in el:
                                    rows.append(_norm_osm(el))
                            except Exception:
                                continue
                        self.db.replace_osm(rows)
                        self._last_at = time.time()
                        self._last_key = key
                        Logger.info('OSM loaded %d places' % len(rows))
                        self.busy = False
                        if on_done:
                            on_done(True)
                    except Exception:
                        _bad()
                def _bad(*a):
                    Logger.error('OSM mirror failed: %s' % url)
                    try_mirror(idx + 1)
                try:
                    from kivy.network.urlrequest import UrlRequest
                    UrlRequest(url, on_success=_ok, on_error=_bad,
                               on_failure=_bad, timeout=20)
                except Exception:
                    _bad()
            try_mirror(0)

    # ----------------------- офлайн-база РФ -----------------------
    class BaseDB:
        def __init__(self):
            self.conn = None
            self.debug = ''
            self.path = self._find()
            if self.path:
                try:
                    self.conn = sqlite3.connect('file:%s?mode=ro' % self.path, uri=True)
                    self.conn.row_factory = sqlite3.Row
                except Exception:
                    try:
                        self.conn = sqlite3.connect(self.path)
                        self.conn.row_factory = sqlite3.Row
                    except Exception:
                        self.conn = None
        def _ud(self):
            try:
                app = App.get_running_app()
                if app:
                    return app.user_data_dir
            except Exception:
                pass
            try:
                from jnius import autoclass
                PA = autoclass('org.kivy.android.PythonActivity')
                return PA.mActivity.getFilesDir().getAbsolutePath()
            except Exception:
                return '.'
        def _copy_asset(self, asset_name, out_path):
            try:
                from jnius import autoclass
                PA = autoclass('org.kivy.android.PythonActivity')
                FOS = autoclass('java.io.FileOutputStream')
                JBytes = autoclass('[B')
                assets = PA.mActivity.getAssets()
                try:
                    ins = assets.open(asset_name)
                except Exception:
                    return False
                fos = FOS(out_path)
                try:
                    buf = JBytes(65536)
                    while True:
                        n = ins.read(buf)
                        if n <= 0:
                            break
                        fos.write(buf, 0, n)
                finally:
                    try:
                        ins.close()
                    except Exception:
                        pass
                    try:
                        fos.close()
                    except Exception:
                        pass
                return os.path.exists(out_path) and os.path.getsize(out_path) > 0
            except Exception:
                return False
        def _find(self):
            cands = []
            try:
                d = os.path.dirname(os.path.abspath(__file__))
                cands.append(os.path.join(d, 'places_ru.db'))
                cands.append(os.path.join(d, 'ru_data', 'places_ru.db'))
            except Exception:
                pass
            u = self._ud()
            cands.append(os.path.join(u, 'places_ru.db'))
            cands.append(os.path.join(u, 'ru_data', 'places_ru.db'))
            seen = set()
            for p in cands:
                if p in seen:
                    continue
                seen.add(p)
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    self.debug = 'найден: %s' % p
                    return p
            if platform == 'android':
                out = os.path.join(u, 'places_ru.db')
                for asset in ('ru_data/places_ru.db', 'places_ru.db',
                              'app/ru_data/places_ru.db'):
                    if self._copy_asset(asset, out):
                        self.debug = 'скопирован из assets: %s' % asset
                        return out
            self.debug = 'искали: ' + ' | '.join(cands)
            return None
        def near(self, lat, lon, radius_m):
            if not self.conn:
                return []
            dlat = radius_m / 111320.0
            dlon = radius_m / (111320.0 * max(0.01, math.cos(math.radians(lat))))
            try:
                rows = self.conn.execute(
                    'SELECT id, name, type, lat, lon, address, hours_open, '
                    'hours_close, open_24, phone FROM places '
                    'WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?',
                    (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
            except Exception:
                return []
            out = []
            for r in rows:
                d = dict(r)
                d['source'] = 'base'
                ho = (d.get('hours_open') or '').strip()
                hc = (d.get('hours_close') or '').strip()
                d['hours_known'] = 1 if (
                    d.get('open_24') or (ho and (ho, hc) != ('00:00', '00:00'))
                ) else 0
                out.append(d)
            return out
        def close(self):
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass

    # ----------------------- декодер полилиний Valhalla -----------------------
    def _decode_polyline(shape, precision=6):
        data = shape.encode('ascii') if isinstance(shape, str) else bytes(shape)
        factor = 10 ** precision
        lat = 0; lon = 0; i = 0; n = len(data)
        pts = []
        while i < n:
            shift = 0; result = 0
            while True:
                b = data[i] - 63; i += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            lat += ~(result >> 1) if (result & 1) else (result >> 1)
            shift = 0; result = 0
            while True:
                b = data[i] - 63; i += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            lon += ~(result >> 1) if (result & 1) else (result >> 1)
            pts.append((lat / float(factor), lon / float(factor)))
        return pts

    def _decode_shape_auto(shape, origin):
        for prec in (6, 5):
            try:
                pts = _decode_polyline(shape, prec)
            except Exception:
                continue
            if not pts:
                continue
            d = RouteCalculator.haversine(origin[0], origin[1], pts[0][0], pts[0][1])
            if d < 2000:
                return pts
        return _decode_polyline(shape, 6)

    # ----------------------- навигация / звонок -----------------------
    def open_navigation(lat, lon, name):
        if platform != 'android':
            return False
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            act = PythonActivity.mActivity
            uri = Uri.parse('geo:%f,%f?q=%f,%f(%s)' % (lat, lon, lat, lon, name))
            it = Intent(Intent.ACTION_VIEW, uri)
            it.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            act.startActivity(it)
            return True
        except Exception as e:
            Logger.error('navigation intent error: %s' % e)
            return False

    def open_call(phone):
        if platform != 'android' or not phone:
            return False
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            act = PythonActivity.mActivity
            it = Intent(Intent.ACTION_DIAL, Uri.parse('tel:' + phone))
            it.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            act.startActivity(it)
            return True
        except Exception as e:
            Logger.error('call intent error: %s' % e)
            return False

    # ----------------------- карточка заведения -----------------------
    class PlaceCard(ButtonBehavior, BoxLayout):
        def __init__(self, rec, user, on_open, **kw):
            super().__init__(orientation='vertical', size_hint_y=None,
                             height=dp(102), spacing=0, **kw)
            self.rec = rec
            try:
                col = type_color(rec.get('type'))
                open_now = is_open_now(rec)
                dist = RouteCalculator.distance_to(user, rec)
                with self.canvas.before:
                    Color(*col)
                    self._stripe = Rectangle(size=(dp(6), 1), pos=self.pos)
                    Color(0.12, 0.13, 0.17, 1)
                    self._bg = RoundedRectangle(size=self.size, pos=self.pos, radius=[dp(8)])
                self.bind(pos=self._sync_rect, size=self._sync_rect)
                pad = BoxLayout(orientation='vertical', spacing=dp(2),
                                padding=[dp(12), dp(8), dp(12), dp(8)])
                top = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(6))
                top.add_widget(_tag(type_label(rec.get('type')), col, font_size='11sp'))
                if open_now is True:
                    top.add_widget(_tag('ОТКРЫТО', COLOR_OPEN, font_size='11sp'))
                elif open_now is False:
                    top.add_widget(_tag('ЗАКРЫТО', COLOR_CLOSED, font_size='11sp'))
                else:
                    top.add_widget(_tag('ЧАСЫ?', COLOR_UNKNOWN, font_size='11sp'))
                top.add_widget(Widget())
                d_l = Label(text=fmt_dist(dist), font_size='20sp', bold=True, color=col,
                            halign='right', valign='top',
                            size_hint=(None, 1), width=dp(78))
                d_l.bind(width=lambda i, w: setattr(i, 'text_size', (max(10, w), None)))
                top.add_widget(d_l)
                pad.add_widget(top)
                pad.add_widget(_wrap('[b]%s[/b]' % rec.get('name', ''), '15sp',
                                     (0.97, 0.98, 1, 1), markup=True, bold=True,
                                     shorten=True))
                bot = BoxLayout(size_hint_y=None, height=dp(18), spacing=dp(6))
                bot.add_widget(_wrap(rec.get('address', '') or '', '12sp',
                                     (0.72, 0.75, 0.82, 1), shorten=True))
                hrs = 'круглосуточно' if rec.get('open_24') else (
                    ('%s–%s' % (rec.get('hours_open', ''), rec.get('hours_close', '')))
                    if rec.get('hours_known', 1) else 'часы неизвестны')
                h_l = Label(text=hrs, font_size='11sp', color=(0.60, 0.63, 0.70, 1),
                            halign='right', valign='middle',
                            size_hint=(None, 1), width=dp(96))
                h_l.bind(width=lambda i, w: setattr(i, 'text_size', (max(10, w), None)))
                bot.add_widget(h_l)
                pad.add_widget(bot)
                self.add_widget(pad)
                self.bind(on_release=lambda *a: on_open(rec))
            except Exception:
                self.clear_widgets()
                self.height = dp(80)
                self.add_widget(_wrap('Ошибка карточки:\n' + _tb_text(), '12sp',
                                      (1, 0.6, 0.6, 1)))
        def _sync_rect(self, *a):
            try:
                self._stripe.pos = self.pos
                self._stripe.size = (dp(6), self.height)
                self._bg.pos = self.pos
                self._bg.size = self.size
            except Exception:
                pass

    # ----------------------- оверлей ошибок -----------------------
    class ErrorOverlay(Widget):
        def __init__(self, **k):
            super().__init__(**k)
            self.size_hint = (None, None)
            self.size = (0, 0)
        def show(self, title, body):
            self.clear_widgets()
            self.size_hint = (1, 1)
            box = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
            with box.canvas.before:
                Color(0.05, 0.06, 0.09, 0.97)
                self._r = Rectangle()
            box.bind(pos=self._sync, size=self._sync)
            self._sync(box, box.size)
            ttl = Label(text='[b]%s[/b]' % title, markup=True, size_hint_y=None,
                        height=dp(44), color=(1, 0.85, 0.3, 1), font_size='15sp',
                        halign='left', valign='middle')
            ttl.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
            sv = ScrollView(bar_width=dp(4), bar_color=(0.3, 0.6, 1, 0.6), do_scroll_y=True)
            lbl = Label(text=body, size_hint_y=None, halign='left', valign='top',
                        font_size='12sp', color=(0.9, 0.9, 0.9, 1), markup=False)
            lbl.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
            lbl.bind(texture_size=lambda i, v: setattr(i, 'height', v[1] + dp(12)))
            sv.add_widget(lbl)
            close = Button(text='Закрыть', size_hint_y=None, height=dp(50), bold=True,
                           background_color=(0.30, 0.40, 0.50, 1))
            close.bind(on_press=lambda *a: self.hide())
            box.add_widget(ttl)
            box.add_widget(sv)
            box.add_widget(close)
            self.add_widget(box)
        def _sync(self, inst, val):
            try:
                self._r.pos = inst.pos
                self._r.size = inst.size
            except Exception:
                pass
        def hide(self):
            self.clear_widgets()
            self.size_hint = (None, None)
            self.size = (0, 0)

    # ----------------------- экран: список -----------------------
    class MainScreen(Screen):
        def __init__(self, db, osm, base, **kw):
            super().__init__(**kw)
            self.db = db
            self.osm = osm
            self.base = base
            self.user = _load_last_fix()
            self.type_filter = 'all'
            self.mode_filter = 'all'
            self._rows_cache = []
            self._tap_start = None
            self._tap_ev = None
            self._reload_ev = None
            self.loc = LocationService()
            root = BoxLayout(orientation='vertical', spacing=0)
            header = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(31),
                               padding=[dp(16), dp(3), dp(16), dp(3)], spacing=dp(2))
            with header.canvas.before:
                Color(0.10, 0.11, 0.16, 1)
                self._hdr = Rectangle(size=header.size, pos=header.pos)
            header.bind(pos=lambda i, v: setattr(self._hdr, 'pos', v),
                        size=lambda i, v: setattr(self._hdr, 'size', v))
            if self.user:
                loc0 = 'Вы: %.5f, %.5f' % (self.user['lat'], self.user['lon'])
            else:
                loc0 = 'Местоположение: не определено'
            self.loc_label = _auto_label(loc0, font_size='12sp',
                                         color=(0.95, 0.78, 0.35, 1))
            header.add_widget(self.loc_label)
            root.add_widget(header)
            filt = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(96),
                             padding=[dp(12), dp(5), dp(12), dp(5)], spacing=dp(4))
            row_type = BoxLayout(size_hint_y=None, height=dp(39), spacing=dp(6))
            self.tb_type = {}
            for key, title in (('all', 'Все'), ('pharmacy', 'Аптеки'), ('atm', 'Банкоматы')):
                tb = ToggleButton(text=title, group='type', font_size='12sp', bold=True,
                                  shorten=False, halign='center', valign='middle',
                                  state=('down' if key == 'all' else 'normal'))
                tb.bind(size=lambda inst, v: setattr(
                    inst, 'text_size', (max(10, v[0] - dp(8)), None)))
                tb.bind(on_press=lambda inst, k=key: self._set_type(k))
                self.tb_type[key] = tb
                row_type.add_widget(tb)
            filt.add_widget(row_type)
            row_mode = BoxLayout(size_hint_y=None, height=dp(39), spacing=dp(6))
            self.tb_mode = {}
            for key, title in (('all', 'Все'), ('open', 'Открыто сейчас'), ('24', '24 часа')):
                tb = ToggleButton(text=title, group='mode', font_size='12sp', bold=True,
                                  shorten=False, halign='center', valign='middle',
                                  state=('down' if key == 'all' else 'normal'))
                tb.bind(size=lambda inst, v: setattr(
                    inst, 'text_size', (max(10, v[0] - dp(8)), None)))
                tb.bind(on_press=lambda inst, k=key: self._set_mode(k))
                self.tb_mode[key] = tb
                row_mode.add_widget(tb)
            filt.add_widget(row_mode)
            root.add_widget(filt)
            self._first_fix = self.user is None
            self.map = make_map(15 if self.user else 2)
            if self.map is not None:
                self.map.size_hint_y = 1
                self.map.bind(on_touch_down=self._td, on_touch_up=self._tu)
                root.add_widget(self.map)
            self.markers = []
            self.user_marker = None
            self.list_scroll = ScrollView(size_hint_y=None, height=dp(224),
                                          do_scroll_y=True, bar_width=dp(4),
                                          bar_color=(0.3, 0.6, 1, 0.5))
            self.list_grid = GridLayout(cols=1, spacing=dp(8), size_hint_y=None,
                                        padding=[dp(10), dp(8)])
            self.list_grid.bind(minimum_height=self.list_grid.setter('height'))
            self.list_scroll.add_widget(self.list_grid)
            root.add_widget(self.list_scroll)
            bar = BoxLayout(size_hint_y=None, height=dp(35), spacing=dp(6),
                            padding=[dp(8), dp(4), dp(8), dp(4)])
            self.btn_loc = _btn('Моё место', (0.18, 0.45, 0.80, 1), height=dp(27), font_size='12sp')
            self.btn_loc.bind(on_press=lambda *a: self.refresh_location())
            self.btn_add = _btn('+', (0.20, 0.55, 0.30, 1), height=dp(27), font_size='14sp')
            self.btn_add.bind(on_press=lambda *a: self.go_add())
            self.btn_map = _btn('На карте', (0.30, 0.40, 0.50, 1), height=dp(27), font_size='12sp')
            self.btn_map.bind(on_press=lambda *a: self.go_map())
            bar.add_widget(self.btn_loc)
            bar.add_widget(self.btn_add)
            bar.add_widget(self.btn_map)
            root.add_widget(bar)
            self.add_widget(root)
        def _td(self, mv, touch):
            try:
                if self._tap_ev is not None:
                    self._tap_ev.cancel()
                    self._tap_ev = None
            except Exception:
                pass
            self._tap_start = (touch.x, touch.y)
        def _tu(self, mv, touch):
            try:
                if self._tap_start is None:
                    return
                dx = touch.x - self._tap_start[0]; dy = touch.y - self._tap_start[1]
                self._tap_start = None
                if math.hypot(dx, dy) > dp(10):
                    return
                rec = self._hit(mv, touch)
                if rec is not None:
                    # замедленная реакция: карточка откроется через UI_TAP_DELAY
                    self._tap_ev = Clock.schedule_once(
                        lambda dt: self._open(rec, True), UI_TAP_DELAY)
            except Exception:
                pass
        def _hit(self, mv, touch):
            best = None; bestd = 1e9
            for r in self._rows_cache:
                try:
                    x, y = mv.get_window_xy_from(r['lat'], r['lon'])
                except Exception:
                    continue
                d = math.hypot(touch.x - x, touch.y - y)
                if d < bestd:
                    bestd = d; best = r
            if best is not None and bestd <= dp(30):
                return best
            return None
        def _set_user_marker(self, lat, lon):
            if self.map is None:
                return
            try:
                if self.user_marker is None:
                    self.user_marker = MapMarker(lat=lat, lon=lon)
                    _map_add_marker(self.map, self.user_marker)
                else:
                    self.user_marker.lat = lat
                    self.user_marker.lon = lon
            except Exception:
                pass
        def _set_type(self, k):
            try:
                self.type_filter = k
                self._delayed_reload()
            except Exception:
                _show_app_error('_set_type', _tb_text())
        def _set_mode(self, k):
            try:
                self.mode_filter = k
                self._delayed_reload()
            except Exception:
                _show_app_error('_set_mode', _tb_text())
        def _delayed_reload(self):
            try:
                if self._reload_ev is not None:
                    self._reload_ev.cancel()
            except Exception:
                pass
            self._reload_ev = Clock.schedule_once(
                lambda dt: self._reload(), UI_FILTER_DELAY)
        def on_enter(self, *a):
            try:
                self._reload()
                self.refresh_location()
                self._maybe_load_osm()
            except Exception:
                _show_app_error('on_enter', _tb_text())
        def _maybe_load_osm(self, force=False):
            if not self.user:
                return
            self.osm.load(self.user['lat'], self.user['lon'],
                          on_done=lambda ok: self._reload(), force=force)
        def refresh_location(self, *a):
            try:
                self.btn_loc.disabled = True
                self.loc_label.text = 'Определяем местоположение…'
                self.loc.request(self._on_user_fix, self._on_loc_status)
                Clock.schedule_once(
                    lambda dt: setattr(self.btn_loc, 'disabled', False), 20.0)
            except Exception:
                _show_app_error('refresh_location', _tb_text())
        def _on_loc_status(self, msg):
            try:
                self.loc_label.text = msg
            except Exception:
                pass
        def _on_user_fix(self, fix):
            try:
                first = self._first_fix
                self.user = fix
                self._first_fix = False
                _save_last_fix(fix)
                self.loc_label.text = 'Вы: %.5f, %.5f (±%.0f м)' % (
                    fix['lat'], fix['lon'], fix.get('accuracy', 0))
                self.btn_loc.disabled = False
                if self.map is not None:
                    try:
                        if first:
                            self.map.zoom = 15
                        self.map.center_on(fix['lat'], fix['lon'])
                        self._set_user_marker(fix['lat'], fix['lon'])
                    except Exception:
                        pass
                self._reload()
                if first:
                    self._maybe_load_osm(force=True)
            except Exception:
                _show_app_error('_on_user_fix', _tb_text())
        def go_map(self):
            try:
                if self.manager:
                    ms = self.manager.get_screen('map')
                    ms.set_data(self._current_rows(), self.user)
                    self.manager.current = 'map'
            except Exception:
                _show_app_error('go_map', _tb_text())
        def go_add(self):
            try:
                if self.manager:
                    ad = self.manager.get_screen('add')
                    ad.set_user(self.user)
                    self.manager.current = 'add'
            except Exception:
                _show_app_error('go_add', _tb_text())
        def _current_rows(self):
            fresh = self.db.all_places()
            real = [r for r in fresh if r.get('source') in ('osm', 'user')]
            base = []
            if self.user and self.base is not None and self.base.conn:
                base = self.base.near(self.user['lat'], self.user['lon'], BASE_RADIUS_M)
                keys = {(round(r['lat'], 3), round(r['lon'], 3)) for r in real}
                base = [r for r in base
                        if (round(r['lat'], 3), round(r['lon'], 3)) not in keys]
            cache = _addr_cache_load()
            merged = []
            seen = set()
            for r in real + base:
                _apply_chain_defaults(r)
                if not (r.get('address') or '').strip():
                    a = cache.get(_addr_key(r))
                    if a:
                        r['address'] = a
                key = ((r.get('name') or '').strip().lower(),
                       (r.get('address') or '').strip().lower() or
                       (round(r['lat'], 3), round(r['lon'], 3)))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(r)
            return filter_places(merged, self.type_filter,
                                 self.mode_filter, self.user)
        def _reload(self):
            try:
                rows = self._current_rows()
                self._rows_cache = rows
                self.list_grid.clear_widgets()
                if not rows:
                    self.list_grid.add_widget(_auto_label(
                        'Рядом ничего не найдено.\nНажмите «Моё место» — список и карта '
                        'наполнятся\n(база РФ из APK + онлайн-обновление вокруг вас).',
                        color=(0.80, 0.82, 0.88, 1)))
                else:
                    for r in rows:
                        try:
                            self.list_grid.add_widget(PlaceCard(r, self.user, self._open))
                        except Exception:
                            self.list_grid.add_widget(_auto_label(
                                'Ошибка элемента списка:\n' + _tb_text(),
                                color=(1, 0.6, 0.6, 1)))
                self._draw_markers(rows)
                if self.user:
                    self._set_user_marker(self.user['lat'], self.user['lon'])
            except Exception:
                _show_app_error('_reload', _tb_text())
        def _draw_markers(self, rows):
            if self.map is None or not MAPVIEW_OK:
                return
            try:
                for m in list(self.markers):
                    _map_remove_marker(self.map, m)
                self.markers = []
                for r in rows[:80]:
                    try:
                        mk = MapMarker(lat=r['lat'], lon=r['lon'])
                        _map_add_marker(self.map, mk)
                        self.markers.append(mk)
                    except Exception:
                        pass
            except Exception:
                Logger.error('draw markers error: %s' % _tb_text())
        def _open(self, rec, autoroute=False):
            try:
                if self.manager:
                    ds = self.manager.get_screen('detail')
                    ds.set_place(rec, self.user, autoroute)
                    self.manager.current = 'detail'
            except Exception:
                _show_app_error('_open', _tb_text())

    # ----------------------- экран: карта со всеми -------------------
    class MapScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.rows = []
            self.user = None
            self.markers = []
            self._tap_start = None
            self._tap_ev = None
            root = BoxLayout(orientation='vertical', spacing=0)
            self.map = make_map(15)
            if self.map is not None:
                self.map.bind(on_touch_down=self._td, on_touch_up=self._tu)
                root.add_widget(self.map, 1)
            else:
                root.add_widget(Label(text='Карта недоступна'), 1)
            bar = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(8), dp(6), dp(8), dp(6)], spacing=dp(6))
            b = _btn('К списку', (0.30, 0.40, 0.50, 1), height=dp(40), font_size='13sp')
            b.bind(on_press=lambda *a: self.back())
            bar.add_widget(b)
            root.add_widget(bar)
            self.add_widget(root)
        def _td(self, mv, touch):
            try:
                if self._tap_ev is not None:
                    self._tap_ev.cancel()
                    self._tap_ev = None
            except Exception:
                pass
            self._tap_start = (touch.x, touch.y)
        def _tu(self, mv, touch):
            try:
                if self._tap_start is None:
                    return
                dx = touch.x - self._tap_start[0]; dy = touch.y - self._tap_start[1]
                self._tap_start = None
                if math.hypot(dx, dy) > dp(10):
                    return
                best = None; bestd = 1e9
                for r in self.rows:
                    try:
                        x, y = mv.get_window_xy_from(r['lat'], r['lon'])
                    except Exception:
                        continue
                    d = math.hypot(touch.x - x, touch.y - y)
                    if d < bestd:
                        bestd = d; best = r
                if best is not None and bestd <= dp(30):
                    rec = best
                    self._tap_ev = Clock.schedule_once(
                        lambda dt: self._open_rec(rec), UI_TAP_DELAY)
            except Exception:
                pass
        def _open_rec(self, rec):
            try:
                if self.manager:
                    ds = self.manager.get_screen('detail')
                    ds.set_place(rec, self.user, True)
                    self.manager.current = 'detail'
            except Exception:
                _show_app_error('MapScreen._open_rec', _tb_text())
        def set_data(self, rows, user):
            self.rows = rows
            self.user = user
        def on_enter(self, *a):
            try:
                for m in list(self.markers):
                    _map_remove_marker(self.map, m)
                self.markers = []
                if self.map is None:
                    return
                if self.user:
                    try:
                        self.map.center_on(self.user['lat'], self.user['lon'])
                    except Exception:
                        pass
                for r in self.rows:
                    try:
                        mk = MapMarker(lat=r['lat'], lon=r['lon'])
                        _map_add_marker(self.map, mk)
                        self.markers.append(mk)
                    except Exception:
                        pass
            except Exception:
                _show_app_error('MapScreen.on_enter', _tb_text())
        def back(self):
            if self.manager:
                self.manager.current = 'main'

    # ----------------------- экран: добавить своё место -------------------
    class AddScreen(Screen):
        def __init__(self, db, **kw):
            super().__init__(**kw)
            self.db = db
            self.user = None
            outer = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(12))
            outer.add_widget(_auto_label(
                '[b]Добавить своё место[/b]\n'
                'Три способа задать точку: 1) вставить координаты из Яндекс/2ГИС/Google '
                '(широта, долгота — через запятую, точку с запятой или пробел); '
                '2) улица + дом (найдём на карте); 3) кнопка «Моё место» на главном экране.',
                markup=True, font_size='14sp', color=(0.95, 0.97, 1, 1)))
            fields = ScrollView(size_hint_y=1, do_scroll_y=True, bar_width=dp(4),
                                bar_color=(0.3, 0.6, 1, 0.4))
            col = GridLayout(cols=1, size_hint_y=None, spacing=dp(8),
                             padding=[0, dp(4), 0, dp(4)])
            col.bind(minimum_height=col.setter('height'))
            self.f_name = _field('Название (например: Аптека Апрель)')
            self.f_coord = _field('Координаты: 55.751000, 37.618000  (необязательно)')
            self.f_street = _field('Улица (например: ул. Тверская)  (необязательно)')
            self.f_house = _field('Дом (например: 12)  (необязательно)', height=dp(44))
            self.f_city = _field('Город (необязательно)')
            self.f_addr = _field('Адрес для показа (необязательно, иначе соберём из улицы)')
            self.f_hours = _field('Часы 08:00-21:00 (необязательно)')
            for w in (self.f_name, self.f_coord, self.f_street, self.f_house,
                      self.f_city, self.f_addr, self.f_hours):
                col.add_widget(w)
            fields.add_widget(col)
            outer.add_widget(fields)
            trow = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(6))
            self.t_ph = ToggleButton(text='Аптека', group='addtype', state='down',
                                     font_size='14sp', bold=True)
            self.t_at = ToggleButton(text='Банкомат', group='addtype', font_size='14sp', bold=True)
            self.t_24 = ToggleButton(text='24 часа', size_hint_x=0.7, font_size='14sp', bold=True)
            trow.add_widget(self.t_ph); trow.add_widget(self.t_at); trow.add_widget(self.t_24)
            outer.add_widget(trow)
            self.status = _auto_label('', font_size='12sp', color=(0.95, 0.78, 0.35, 1))
            outer.add_widget(self.status)
            brow = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(6))
            b_save = _btn('Сохранить', (0.20, 0.55, 0.30, 1), height=dp(52), font_size='14sp')
            b_save.bind(on_press=lambda *a: self.save())
            b_back = _btn('Отмена', (0.35, 0.37, 0.45, 1), height=dp(52), font_size='14sp')
            b_back.bind(on_press=lambda *a: self.back())
            brow.add_widget(b_save); brow.add_widget(b_back)
            outer.add_widget(brow)
            self.add_widget(outer)
        def set_user(self, user):
            self.user = user
            if user:
                self.status.text = 'GPS есть: %.5f, %.5f (±%.0f м). Можно сохранить по GPS, ' \
                                   'или вставить координаты / адрес.' % (
                                       user['lat'], user['lon'], user.get('accuracy', 0))
            else:
                self.status.text = 'GPS не определён — задайте координаты или адрес.'
        def _parse_coords(self, s):
            if not s:
                return None
            toks = re.findall(r'[+-]?\d+(?:[.,]\d+)?', s)
            nums = []
            for t in toks:
                try:
                    nums.append(float(t.replace(',', '.')))
                except Exception:
                    pass
            if len(nums) < 2:
                return None
            a, b = nums[0], nums[1]
            if abs(a) > 90 and abs(b) <= 90:
                a, b = b, a
            if abs(a) > 90 or abs(b) > 180:
                return None
            return (a, b)
        def _geocode(self, street, house, city, cb):
            q = (street + (' ' + house if house else '') +
                 (', ' + city if city else '')).strip()
            url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + quote(q)
            try:
                from kivy.network.urlrequest import UrlRequest
                def ok(req, res):
                    try:
                        data = res if isinstance(res, list) else json.loads(res)
                        if data:
                            cb(float(data[0]['lat']), float(data[0]['lon']))
                        else:
                            self.status.text = 'Адрес не найден на карте (OpenStreetMap).'
                    except Exception:
                        self.status.text = 'Ошибка разбора ответа геокодера.'
                def bad(*a):
                    self.status.text = 'Ошибка геокодера (проверьте интернет).'
                UrlRequest(url, on_success=ok, on_error=bad, on_failure=bad, timeout=15,
                           req_headers={'User-Agent': 'NearbyFinderApp/23 (kivy)'})
            except Exception:
                self.status.text = 'Не удалось запустить геокодер.'
        def _build_addr(self):
            a = self.f_addr.text.strip()
            if a:
                return a
            st = self.f_street.text.strip(); ho = self.f_house.text.strip()
            ci = self.f_city.text.strip()
            return (st + (' ' + ho if ho else '') + (', ' + ci if ci else '')).strip()
        def save(self):
            try:
                name = self.f_name.text.strip()
                if not name:
                    self.status.text = 'Введите название.'
                    return
                typ = 'pharmacy' if self.t_ph.state == 'down' else 'atm'
                o24 = 1 if self.t_24.state == 'down' else 0
                ht = self.f_hours.text.strip()
                if o24:
                    ho, hc, known = '00:00', '00:00', 1
                elif ht:
                    mm = re.search(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', ht)
                    if mm:
                        ho, hc, known = mm.group(1), mm.group(2), 1
                    else:
                        ho, hc, known = '00:00', '00:00', 0
                else:
                    ho, hc, known = '00:00', '00:00', 0
                addr = self._build_addr()
                street = self.f_street.text.strip(); house = self.f_house.text.strip()
                city = self.f_city.text.strip()
                parsed = self._parse_coords(self.f_coord.text.strip())
                if parsed:
                    self._do_save(name, typ, parsed[0], parsed[1], addr, ho, hc, o24, known)
                    return
                if street or house:
                    self.status.text = 'Ищем адрес на карте…'
                    self._geocode(street, house, city,
                                  lambda la, lo: self._do_save(
                                      name, typ, la, lo, addr, ho, hc, o24, known))
                    return
                if self.user:
                    self._do_save(name, typ, self.user['lat'], self.user['lon'],
                                  addr, ho, hc, o24, known)
                    return
                self.status.text = 'Укажите координаты, адрес или включите GPS («Моё место»).'
            except Exception:
                _show_app_error('AddScreen.save', _tb_text())
        def _do_save(self, name, typ, lat, lon, addr, ho, hc, o24, known):
            try:
                self.db.add_place(name, typ, lat, lon, addr, ho, hc, o24, '', known)
            except Exception:
                _show_app_error('AddScreen._do_save', _tb_text())
                return
            self.f_name.text = ''
            self.f_coord.text = ''
            self.f_street.text = ''
            self.f_house.text = ''
            self.f_hours.text = ''
            self.f_addr.text = ''
            self.status.text = 'Сохранено: %s (%.5f, %.5f)' % (name, lat, lon)
            if self.manager:
                try:
                    self.manager.get_screen('main')._reload()
                except Exception:
                    pass
                self.manager.current = 'main'
        def back(self):
            if self.manager:
                self.manager.current = 'main'

    # ----------------------- экран: детали + маршрут -------------------
    class DetailScreen(Screen):
        def __init__(self, db, **kw):
            super().__init__(**kw)
            self.db = db
            self.rec = None
            self.user = None
            self.loc = LocationService()
            self.follow = False
            self.base_info = ''
            self.route_info = ''
            self.route_mode = 'foot'
            self._autoroute = False
            self.m_place = None
            self.user_dot = None
            self.route_markers = []
            self._route_pts = []
            self._user_xy = None
            self._dot_srcs = None
            self._straight = 0
            root = BoxLayout(orientation='vertical', spacing=0)
            self.map = make_map(16)
            if self.map is not None:
                self.map.size_hint_y = 0.45
                root.add_widget(self.map)
                self.map.bind(on_touch_move=self._user_grabbed)
            panel = BoxLayout(orientation='vertical', size_hint_y=0.55,
                              spacing=dp(6), padding=dp(10))
            info_scroll = ScrollView(size_hint_y=1, do_scroll_y=True, bar_width=dp(4),
                                     bar_color=(0.3, 0.6, 1, 0.4))
            self.info = _auto_label('', markup=True, font_size='14sp',
                                    color=(0.95, 0.97, 1, 1))
            info_scroll.add_widget(self.info)
            panel.add_widget(info_scroll)
            btns = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(5))
            self.b_route = _btn('Маршрут', (0.18, 0.55, 0.32, 1), height=dp(44), font_size='12sp')
            self.b_mode = _btn('Пешком', (0.35, 0.37, 0.45, 1), height=dp(44), font_size='12sp')
            self.b_call = _btn('Позвонить', (0.20, 0.45, 0.85, 1), height=dp(44), font_size='12sp')
            self.b_back = _btn('Назад', (0.35, 0.37, 0.45, 1), height=dp(44), font_size='12sp')
            # ширины: «Пешком» чуть шире, «Позвонить» чуть уже — без переносов
            self.b_route.size_hint_x = 1.0
            self.b_mode.size_hint_x = 1.0
            self.b_call.size_hint_x = 1.2
            self.b_back.size_hint_x = 1.0
            self.b_route.bind(on_press=lambda *a: self.route())
            self.b_mode.bind(on_press=lambda *a: self._flip_mode())
            self.b_call.bind(on_press=lambda *a: self.call())
            self.b_back.bind(on_press=lambda *a: self.back())
            for b in (self.b_route, self.b_mode, self.b_call, self.b_back):
                btns.add_widget(b)
            panel.add_widget(btns)
            root.add_widget(panel)
            self.add_widget(root)
        def _flip_mode(self):
            self.route_mode = 'car' if self.route_mode == 'foot' else 'foot'
            self.b_mode.text = 'Пешком' if self.route_mode == 'foot' else 'Авто'
        def _dot_sources(self):
            if self._dot_srcs:
                return self._dot_srcs
            try:
                d = App.get_running_app().user_data_dir
                os.makedirs(d, exist_ok=True)
                p_line = os.path.join(d, 'route_dot.png')
                p_user = os.path.join(d, 'user_dot.png')
                if not os.path.exists(p_line):
                    _write_png(p_line, 14, 14, _px_line)
                if not os.path.exists(p_user):
                    _write_png(p_user, 22, 22, _px_user)
                self._dot_srcs = (p_line, p_user)
            except Exception:
                Logger.error('dot assets error: %s' % _tb_text())
                self._dot_srcs = None
            return self._dot_srcs
        def _user_grabbed(self, *a):
            self.follow = False
        def _refresh_info(self):
            dbg = ''
            try:
                dbg = ('\n[color=556070][тех.инфо build %d] draw=markers pts=%d '
                       'dots=%d user=%s[/color]' % (
                           BUILD, len(self._route_pts), len(self.route_markers),
                           '1' if self._user_xy else '0'))
            except Exception:
                dbg = ''
            self.info.text = self.base_info + (('\n' + self.route_info) if self.route_info else '') + dbg
        def _refresh_base(self):
            r = self.rec
            if not r:
                return
            open_now = is_open_now(r)
            dist = RouteCalculator.distance_to(self.user, r)
            hrs = 'круглосуточно' if r.get('open_24') else (
                ('%s–%s' % (r.get('hours_open', ''), r.get('hours_close', '')))
                if r.get('hours_known', 1) else 'часы неизвестны')
            if open_now is True:
                st = '[color=44cc66]ОТКРЫТО[/color]'
            elif open_now is False:
                st = '[color=d94c4c]ЗАКРЫТО[/color]'
            else:
                st = '[color=8890a0]ЧАСЫ НЕИЗВ.[/color]'
            addr = (r.get('address') or '').strip() or 'адрес уточняется…'
            self.base_info = (
                '[b]%s[/b]  —  %s\n%s\n%s   •   %s   •   %s\nРасстояние: [b]%s[/b]' % (
                    r.get('name', ''), type_label(r.get('type')), addr,
                    st, hrs, r.get('phone', ''), fmt_dist(dist)))
            self._refresh_info()
        def set_place(self, rec, user, autoroute=False):
            self.rec = _apply_chain_defaults(rec)
            self.user = user
            self._autoroute = bool(autoroute)
        def on_enter(self, *a):
            try:
                if not self.rec:
                    return
                self.route_info = ''
                self.follow = False
                self._route_pts = []
                self._user_xy = None
                if self.map is not None:
                    for m in list(self.route_markers):
                        _map_remove_marker(self.map, m)
                    self.route_markers = []
                    if self.user_dot is not None:
                        _map_remove_marker(self.map, self.user_dot)
                        self.user_dot = None
                self._refresh_base()
                self._draw()
                self._enrich_addr()
                if self._autoroute:
                    self._autoroute = False
                    Clock.schedule_once(lambda dt: self.route(), 0.4)
            except Exception:
                _show_app_error('DetailScreen.on_enter', _tb_text())
        def _enrich_addr(self):
            r = self.rec
            if not r or (r.get('address') or '').strip():
                return
            key = _addr_key(r)
            cache = _addr_cache_load()
            if key in cache:
                r['address'] = cache[key]
                self._refresh_base()
                return
            url = ('https://nominatim.openstreetmap.org/reverse?format=json'
                   '&lat=%f&lon=%f&zoom=18&addressdetails=1' % (r['lat'], r['lon']))
            def ok(req, res):
                try:
                    data = res if isinstance(res, dict) else json.loads(res)
                    a = data.get('address', {}) or {}
                    road = a.get('road') or a.get('pedestrian') or a.get('street') or ''
                    house = a.get('house_number') or ''
                    addr = (road + (', ' + house if house else '')).strip()
                    if not addr:
                        addr = (data.get('display_name') or '')[:60]
                    if addr:
                        r['address'] = addr
                        cache[key] = addr
                        _addr_cache_save(cache)
                        self._refresh_base()
                except Exception:
                    pass
            def bad(*a):
                pass
            try:
                from kivy.network.urlrequest import UrlRequest
                UrlRequest(url, on_success=ok, on_error=bad, on_failure=bad, timeout=10,
                           req_headers={'User-Agent': 'NearbyFinderApp/23 (kivy)'})
            except Exception:
                pass
        def on_leave(self, *a):
            try:
                self.follow = False
                self.loc.stop()
            except Exception:
                pass
        def _draw(self):
            if self.map is None or not self.rec:
                return
            r = self.rec
            if self.m_place is not None:
                _map_remove_marker(self.map, self.m_place)
            self.m_place = None
            try:
                self.m_place = MapMarker(lat=r['lat'], lon=r['lon'])
                _map_add_marker(self.map, self.m_place)
                if self.user:
                    self.map.center_on((self.user['lat'] + r['lat']) / 2.0,
                                       (self.user['lon'] + r['lon']) / 2.0)
                else:
                    self.map.center_on(r['lat'], r['lon'])
            except Exception as e:
                Logger.error('detail markers error: %s' % e)
            if self.user:
                self._set_user_dot(self.user['lat'], self.user['lon'])
        def _set_route_dots(self, pts):
            if self.map is None:
                return
            srcs = self._dot_sources()
            for m in list(self.route_markers):
                _map_remove_marker(self.map, m)
            self.route_markers = []
            if not pts:
                return
            step = max(1, len(pts) // 50)
            sel = pts[::step]
            if sel[-1] != pts[-1]:
                sel.append(pts[-1])
            for lat, lon in sel:
                mk = None
                if srcs:
                    try:
                        mk = MapMarker(lat=lat, lon=lon, source=srcs[0])
                    except Exception:
                        mk = None
                if mk is None:
                    try:
                        mk = MapMarker(lat=lat, lon=lon)
                    except Exception:
                        continue
                _map_add_marker(self.map, mk)
                self.route_markers.append(mk)
        def _set_user_dot(self, lat, lon):
            if self.map is None:
                return
            srcs = self._dot_sources()
            try:
                if self.user_dot is None:
                    try:
                        self.user_dot = MapMarker(lat=lat, lon=lon, source=srcs[1]) if srcs else None
                    except Exception:
                        self.user_dot = None
                    if self.user_dot is None:
                        self.user_dot = MapMarker(lat=lat, lon=lon)
                    _map_add_marker(self.map, self.user_dot)
                else:
                    self.user_dot.lat = lat
                    self.user_dot.lon = lon
                self._user_xy = (lat, lon)
            except Exception:
                pass
        def route(self):
            if not self.rec:
                return
            if not self.user:
                self.route_info = ('[color=e0a040]Для маршрута сначала нажмите '
                                   '«Моё место» на главном экране.[/color]')
                self._refresh_info()
                return
            if self.map is None:
                self._fallback_route()
                return
            u = self.user
            r = self.rec
            self._straight = RouteCalculator.haversine(u['lat'], u['lon'],
                                                       r['lat'], r['lon'])
            self._set_user_dot(u['lat'], u['lon'])
            self.route_info = ('Строим маршрут (%s)…' % (
                'пешком' if self.route_mode == 'foot' else 'авто'))
            self._refresh_info()
            if self.route_mode == 'car':
                self._try_osrm_car()
            else:
                self._try_valhalla()
        def _try_valhalla(self):
            u = self.user; r = self.rec
            payload = {'locations': [{'lat': u['lat'], 'lon': u['lon']},
                                     {'lat': r['lat'], 'lon': r['lon']}],
                       'costing': 'pedestrian', 'units': 'kilometers'}
            url = 'https://valhalla1.openstreetmap.de/route?json=' + quote(json.dumps(payload))
            try:
                from kivy.network.urlrequest import UrlRequest
                UrlRequest(url, on_success=self._vh_ok, on_error=self._vh_bad,
                           on_failure=self._vh_bad, timeout=15)
            except Exception:
                self._try_brouter()
        def _vh_ok(self, req, result):
            try:
                data = result if isinstance(result, dict) else json.loads(result)
                legs = data['trip']['legs']
                pts = []
                road_m = 0.0; sec = 0.0
                for leg in legs:
                    pts += _decode_shape_auto(leg['shape'],
                                              (self.user['lat'], self.user['lon']))
                    summ = leg.get('summary', {}) or {}
                    road_m += float(summ.get('length', 0) or 0) * 1000.0
                    sec += float(summ.get('time', 0) or 0)
                if len(pts) < 2:
                    raise ValueError('empty shape')
                self._apply_route(pts, road_m, sec, 'пешеходный маршрут')
            except Exception:
                self._try_brouter()
        def _vh_bad(self, *a):
            self._try_brouter()
        def _try_brouter(self):
            u = self.user; r = self.rec
            url = ('https://brouter.de/brouter?lonlats=%f,%f|%f,%f'
                   '&profile=foot-shortest&format=geojson&alternativeidx=0') % (
                       u['lon'], u['lat'], r['lon'], r['lat'])
            try:
                from kivy.network.urlrequest import UrlRequest
                UrlRequest(url, on_success=self._br_ok, on_error=self._br_bad,
                           on_failure=self._br_bad, timeout=15)
            except Exception:
                self._try_osrm_foot()
        def _br_ok(self, req, result):
            try:
                data = result if isinstance(result, dict) else json.loads(result)
                feat = data['features'][0]
                coords = feat['geometry']['coordinates']
                pts = [(float(c[1]), float(c[0])) for c in coords]
                props = feat.get('properties', {}) or {}
                road_m = float(props.get('track-length', 0) or 0)
                sec = float(props.get('total-time', 0) or 0)
                if len(pts) < 2:
                    raise ValueError('empty geometry')
                self._apply_route(pts, road_m, sec, 'пешеходный маршрут')
            except Exception:
                self._try_osrm_foot()
        def _br_bad(self, *a):
            self._try_osrm_foot()
        def _try_osrm_foot(self):
            u = self.user; r = self.rec
            url = ('https://router.project-osrm.org/route/v1/foot/%f,%f;%f,%f'
                   '?overview=full&geometries=geojson') % (
                       u['lon'], u['lat'], r['lon'], r['lat'])
            try:
                from kivy.network.urlrequest import UrlRequest
                UrlRequest(url, on_success=self._osrm_ok, on_error=self._osrm_bad,
                           on_failure=self._osrm_bad, timeout=12)
            except Exception:
                self._fallback_route()
        def _osrm_ok(self, req, result):
            try:
                data = result if isinstance(result, dict) else json.loads(result)
                route0 = data['routes'][0]
                coords = route0['geometry']['coordinates']
                pts = [(float(c[1]), float(c[0])) for c in coords]
                self._apply_route(pts, float(route0.get('distance') or 0),
                                  float(route0.get('duration') or 0), 'по дорогам')
            except Exception:
                self._fallback_route()
        def _osrm_bad(self, *a):
            self._fallback_route()
        def _try_osrm_car(self):
            u = self.user; r = self.rec
            url = ('https://router.project-osrm.org/route/v1/driving/%f,%f;%f,%f'
                   '?overview=full&geometries=geojson') % (
                       u['lon'], u['lat'], r['lon'], r['lat'])
            try:
                from kivy.network.urlrequest import UrlRequest
                UrlRequest(url, on_success=self._car_ok, on_error=self._car_bad,
                           on_failure=self._car_bad, timeout=12)
            except Exception:
                self._fallback_route()
        def _car_ok(self, req, result):
            try:
                data = result if isinstance(result, dict) else json.loads(result)
                route0 = data['routes'][0]
                coords = route0['geometry']['coordinates']
                pts = [(float(c[1]), float(c[0])) for c in coords]
                self._apply_route(pts, float(route0.get('distance') or 0),
                                  float(route0.get('duration') or 0),
                                  'по дорогам (авто)', check=False)
            except Exception:
                self._fallback_route()
        def _car_bad(self, *a):
            self._fallback_route()
        def _apply_route(self, pts, road_m, sec, label, check=True):
            try:
                straight = self._straight or 0
                if check and 0 < straight < 800 and road_m > straight * 3.0:
                    self._straight_route()
                    return
                self._route_pts = pts
                self._set_route_dots(pts)
                extra = '  •  по дорогам: %s' % fmt_dist(road_m)
                if sec and sec > 0:
                    extra += '  •  ~%d мин' % max(1, int(round(sec / 60.0)))
                else:
                    extra += '  •  ~%d мин пешком' % max(
                        1, int(round(road_m / WALK_SPEED_MPM)))
                self.route_info = (
                    '[color=44cc66]Маршрут построен[/color]  •  %s' % label + extra +
                    '\n[color=8fb4ff]Следование включено: голубая точка — это вы. '
                    'Потяните карту, чтобы взять управление.[/color]')
                self._refresh_info()
                self._fit(pts)
                self._start_follow()
            except Exception:
                self._fallback_route()
        def _straight_route(self):
            try:
                u = self.user
                r = self.rec
                pts = [(u['lat'], u['lon']), (r['lat'], r['lon'])]
                self._route_pts = pts
                self._set_route_dots(pts)
                d = RouteCalculator.haversine(u['lat'], u['lon'], r['lat'], r['lon'])
                self.route_info = (
                    '[color=44cc66]Маршрут построен[/color]  •  напрямую: %s'
                    '  •  ~%d мин пешком'
                    '\n[color=8fb4ff]Следование включено: голубая точка — это вы. '
                    'Потяните карту, чтобы взять управление.[/color]' % (
                        fmt_dist(d), max(1, int(round(d / WALK_SPEED_MPM)))))
                self._refresh_info()
                self._fit(pts)
                self._start_follow()
            except Exception:
                self._fallback_route()
        def _start_follow(self):
            self.follow = True
            if self.user:
                self._set_user_dot(self.user['lat'], self.user['lon'])
            try:
                self.loc.request(self._on_follow_fix, None, continuous=True)
            except Exception:
                _show_app_error('_start_follow', _tb_text())
        def _on_follow_fix(self, fix):
            try:
                self.user = fix
                self._set_user_dot(fix['lat'], fix['lon'])
                if self.follow and self.map is not None:
                    try:
                        self.map.center_on(fix['lat'], fix['lon'])
                    except Exception:
                        pass
            except Exception:
                pass
        def _fallback_route(self):
            if not self.rec:
                return
            if self.user:
                pts = [(self.user['lat'], self.user['lon']),
                       (self.rec['lat'], self.rec['lon'])]
                self._route_pts = pts
                self._set_route_dots(pts)
                self.route_info = (
                    '[color=e0a070]Онлайн-маршрут недоступен — '
                    'показана прямая линия до точки.[/color]')
                self._refresh_info()
                self._fit(pts)
                self._start_follow()
            else:
                self.route_info = (
                    '[color=d98c8c]Онлайн-маршрут недоступен — '
                    'нет вашего местоположения.[/color]')
                self._refresh_info()
        def _fit(self, pts):
            try:
                if not pts:
                    return
                lats = [p[0] for p in pts]
                lons = [p[1] for p in pts]
                self.map.center_on(sum(lats) / len(lats), sum(lons) / len(lons))
            except Exception:
                pass
        def call(self):
            if not self.rec:
                return
            open_call(self.rec.get('phone', ''))
        def back(self):
            try:
                self.follow = False
                self.loc.stop()
            except Exception:
                pass
            if self.manager:
                self.manager.current = 'main'

    # ----------------------- приложение -----------------------
    def _show_app_error(where, text):
        try:
            app = App.get_running_app()
            if app is not None and hasattr(app, 'show_error'):
                app.show_error('Ошибка в %s (build %d)' % (where, BUILD), text)
        except Exception:
            pass
        _save_crash('--- %s ---\n%s' % (where, text))

    class NearbyFinderApp(App):
        def build(self):
            try:
                self.db = Database(os.path.join(self.user_data_dir, 'places.db'))
            except Exception as e:
                Logger.error('DB init error: %s' % e)
                self.db = Database('places.db')
            self.osm = OsmLoader(self.db)
            self.base = BaseDB()
            sm = ScreenManager()
            sm.add_widget(MainScreen(self.db, self.osm, self.base, name='main'))
            sm.add_widget(MapScreen(name='map'))
            sm.add_widget(DetailScreen(self.db, name='detail'))
            sm.add_widget(AddScreen(self.db, name='add'))
            self.sm = sm
            self.overlay = ErrorOverlay()
            fl = FloatLayout()
            fl.add_widget(sm)
            fl.add_widget(self.overlay)
            return fl
        def show_error(self, title, body):
            try:
                self.overlay.show(title, body)
            except Exception:
                print('OVERLAY FAIL build=%d:\n%s\n%s' % (BUILD, title, body), flush=True)
        def on_start(self):
            if platform == 'android':
                Clock.schedule_once(lambda dt: request_android_permissions(), 1.0)
        def on_pause(self):
            return True
        def on_stop(self):
            for sn in ('main', 'detail'):
                try:
                    self.sm.get_screen(sn).loc.stop()
                except Exception:
                    pass
            if getattr(self, 'db', None):
                try:
                    self.db.close()
                except Exception:
                    pass
            if getattr(self, 'base', None):
                try:
                    self.base.close()
                except Exception:
                    pass

    NearbyFinderApp().run()
except BaseException:
    err = _tb_text()
    _save_crash(err)
    _run_error_app('Ошибка запуска приложения', err)
