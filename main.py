"""
Small URL Python — Acortador de URLs con Tkinter
=================================================
Aplicación de escritorio que combina:
  • Ventana Tkinter para crear/gestionar enlaces
  • Servidor HTTP embebido (puerto 8080) que sirve redirecciones 301 REALES
  • Base de datos SQLite para persistir enlaces y clics
  • Generador de códigos QR por enlace

Instalación:
    pip install qrcode[pil]
    (Flask NO es necesario, esto usa la librería estándar)

Ejecución:
    python smallurl.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
import threading
import secrets
import string
import hashlib
import os
import sys
import csv
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from io import BytesIO

# --- DPI awareness en Windows ---
if sys.platform == 'win32':
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# --- Dependencias opcionales ---
try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(BASE_DIR, 'smallurl.db')
SERVER_HOST  = '127.0.0.1'
SERVER_PORT  = 8080
BASE_URL     = f'http://{SERVER_HOST}:{SERVER_PORT}'
ALPHABET     = string.ascii_letters + string.digits
RESERVED     = {'api', 'admin', 'panel', 'qr', 'static',
                'favicon.ico', 'index.html', 'new'}

# Paleta de colores
BG        = '#1e1e2e'
SURFACE   = '#2a2a3e'
SURFACE_2 = '#32324a'
BORDER    = '#3a3a52'
TEXT      = '#e0e0ea'
MUTED     = '#8888a0'
ACCENT    = '#6366f1'
ACCENT_H  = '#818cf8'
OK        = '#10b981'
WARN      = '#f59e0b'
ERR       = '#ef4444'


# ---------------------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS links (
                code         TEXT PRIMARY KEY,
                url          TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                active       INTEGER NOT NULL DEFAULT 1,
                pass_hash    TEXT,
                expires_at   TEXT,
                tags         TEXT DEFAULT '',
                total_clicks INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS clicks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT NOT NULL,
                clicked_at TEXT NOT NULL,
                ip         TEXT,
                user_agent TEXT,
                referer    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
            CREATE INDEX IF NOT EXISTS idx_clicks_time ON clicks(clicked_at);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------
def gen_code(n=6):
    return ''.join(secrets.choice(ALPHABET) for _ in range(n))


def unique_code(conn):
    for _ in range(200):
        c = gen_code()
        if c.lower() in RESERVED:
            continue
        if not conn.execute("SELECT 1 FROM links WHERE code=?", (c,)).fetchone():
            return c
    return secrets.token_hex(6)


def normalize_url(raw):
    raw = (raw or '').strip()
    if not raw:
        raise ValueError("La URL no puede estar vacía.")
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw
    p = urlparse(raw)
    if p.scheme not in ('http', 'https') or not p.netloc or '.' not in p.netloc:
        raise ValueError("URL no válida. Ejemplo: https://youtube.com/@usuario")
    return raw


def hash_pwd(p):
    return hashlib.sha256(p.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# SERVIDOR HTTP EMBEBIDO (redirecciones 301 reales)
# ---------------------------------------------------------------------------
class RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Silencioso

    def do_GET(self):
        path = self.path.strip('/').split('?')[0].split('#')[0]

        # Página raíz informativa
        if not path or path == 'index.html':
            self._send_home()
            return

        # Buscar el código
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM links WHERE code=?", (path,)).fetchone()

            if not row:
                self._send_error(404, "Enlace no encontrado",
                                 f"El código '{path}' no existe en la base de datos.")
                return

            if not row['active']:
                self._send_error(410, "Enlace desactivado",
                                 "Este enlace ha sido desactivado temporalmente.")
                return

            if row['expires_at']:
                try:
                    exp = datetime.fromisoformat(row['expires_at'])
                    if datetime.now() > exp:
                        self._send_error(410, "Enlace caducado",
                            f"Expiró el {exp.strftime('%d/%m/%Y a las %H:%M')}.")
                        return
                except Exception:
                    pass

            # Registrar clic
            conn.execute(
                "INSERT INTO clicks (code, clicked_at, ip, user_agent, referer) "
                "VALUES (?, ?, ?, ?, ?)",
                (path,
                 datetime.now().isoformat(timespec='seconds'),
                 self.client_address[0],
                 self.headers.get('User-Agent', '')[:500],
                 self.headers.get('Referer', '')[:500])
            )
            conn.execute(
                "UPDATE links SET total_clicks = total_clicks + 1 WHERE code=?",
                (path,)
            )
            conn.commit()

            # ✅ REDIRECCIÓN 301 REAL
            self.send_response(301)
            self.send_header('Location', row['url'])
            self.send_header('Cache-Control', 'no-cache, no-store')
            self.end_headers()

        finally:
            conn.close()

    # ---------- HTML auxiliares ----------
    def _send_home(self):
        html = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
        <title>Small URL Python</title>
        <style>
            body{font-family:system-ui,sans-serif;background:#1e1e2e;color:#e0e0ea;
                 display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}
            .box{max-width:520px;text-align:center;background:#2a2a3e;
                 border:1px solid #3a3a52;border-radius:16px;padding:44px 32px}
            h1{margin:0 0 12px;font-size:22px}
            h1 span{color:#818cf8}
            p{color:#8888a0;line-height:1.6;margin:8px 0}
            code{background:#1a1a2a;padding:3px 8px;border-radius:6px;color:#a5b4fc}
        </style></head><body>
        <div class="box">
            <h1>&#128279; Small <span>URL Python</span></h1>
            <p>Servidor de redirecciones activo.</p>
            <p>Abre un enlace del tipo<br>
               <code>http://127.0.0.1:8080/codigo</code></p>
        </div></body></html>"""
        data = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code, title, msg):
        html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
        <title>{title}</title>
        <style>
            body{{font-family:system-ui,sans-serif;background:#1e1e2e;color:#e0e0ea;
                  display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
            .box{{max-width:480px;text-align:center;background:#2a2a3e;
                  border:1px solid #3a3a52;border-radius:16px;padding:40px 32px}}
            .icon{{width:64px;height:64px;margin:0 auto 20px;border-radius:50%;
                   display:grid;place-items:center;font-size:28px;
                   background:linear-gradient(135deg,#fca5a5,#ef4444);color:#1a0a0a}}
            h1{{margin:0 0 12px;font-size:20px}}
            p{{color:#8888a0;line-height:1.6;margin:0}}
        </style></head><body>
        <div class="box">
            <div class="icon">&#10005;</div>
            <h1>{title}</h1>
            <p>{msg}</p>
        </div></body></html>"""
        data = html.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_http_server():
    """Arranca el servidor HTTP en un hilo daemon."""
    def run():
        try:
            server = HTTPServer((SERVER_HOST, SERVER_PORT), RedirectHandler)
            server.serve_forever()
        except OSError as e:
            print(f"[servidor] Error al iniciar en {SERVER_HOST}:{SERVER_PORT}: {e}")
    t = threading.Thread(target=run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# INTERFAZ TKINTER
# ---------------------------------------------------------------------------
class SmallURLApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Small URL Python · Acortador")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)
        self.root.configure(bg=BG)

        self._apply_styles()
        init_db()
        start_http_server()
        self._build_ui()
        self.refresh()

    # ---------------- Estilos ttk ----------------
    def _apply_styles(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('Treeview',
                        background=SURFACE,
                        fieldbackground=SURFACE,
                        foreground=TEXT,
                        borderwidth=0,
                        rowheight=30,
                        font=('Segoe UI', 9))
        style.configure('Treeview.Heading',
                        background=SURFACE_2,
                        foreground=MUTED,
                        borderwidth=0,
                        font=('Segoe UI', 9, 'bold'),
                        padding=8)
        style.map('Treeview',
                  background=[('selected', ACCENT)],
                  foreground=[('selected', 'white')])
        style.map('Treeview.Heading',
                  background=[('active', BORDER)])

        style.configure('Vertical.TScrollbar',
                        background=SURFACE,
                        troughcolor=SURFACE,
                        borderwidth=0,
                        arrowcolor=MUTED)

    # ---------------- Layout ----------------
    def _build_ui(self):
        # ============== HEADER ==============
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill='x', padx=22, pady=(18, 6))

        tk.Label(header, text="🔗 Small URL Python", bg=BG, fg=TEXT,
                 font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        tk.Label(header,
                 text=f"Servidor de redirecciones 301 activo en  {BASE_URL}",
                 bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(anchor='w', pady=(2, 0))

        # ============== FORMULARIO ==============
        form = tk.Frame(self.root, bg=SURFACE,
                        highlightbackground=BORDER, highlightthickness=1)
        form.pack(fill='x', padx=22, pady=(14, 10))

        inner = tk.Frame(form, bg=SURFACE)
        inner.pack(fill='x', padx=18, pady=16)

        # URL
        tk.Label(inner, text="URL LARGA", bg=SURFACE, fg=MUTED,
                 font=('Segoe UI', 8, 'bold')).pack(anchor='w')
        self.entry_url = tk.Entry(inner, bg=SURFACE_2, fg=TEXT,
                                   insertbackground=TEXT, relief='flat',
                                   font=('Segoe UI', 10), bd=0)
        self.entry_url.pack(fill='x', ipady=8, pady=(4, 12))
        self.entry_url.insert(0, "https://youtube.com/@DavidGuzmanYT-n2e")

        # Campos secundarios
        row2 = tk.Frame(inner, bg=SURFACE)
        row2.pack(fill='x')
        self.entry_code = self._labeled_entry(row2, "CÓDIGO", "opcional", width=16)
        self.entry_pass = self._labeled_entry(row2, "CONTRASEÑA", "opcional", width=16)
        self.entry_exp  = self._labeled_entry(row2, "CADUCA", "YYYY-MM-DD", width=14)
        self.entry_tags = self._labeled_entry(row2, "ETIQUETAS", "separadas por comas", width=24)

        # Botón + mensaje
        row3 = tk.Frame(inner, bg=SURFACE)
        row3.pack(fill='x', pady=(14, 0))

        self.btn_create = tk.Button(row3, text="🔗  Acortar URL",
                                    bg=ACCENT, fg='white',
                                    activebackground=ACCENT_H, activeforeground='white',
                                    relief='flat', bd=0, cursor='hand2',
                                    font=('Segoe UI', 10, 'bold'),
                                    padx=22, pady=10,
                                    command=self.create_link)
        self.btn_create.pack(side='left')

        self.lbl_result = tk.Label(row3, text="", bg=SURFACE, fg=OK,
                                    font=('Segoe UI', 10, 'bold'))
        self.lbl_result.pack(side='left', padx=(16, 0))

        # ============== STATS ==============
        stats = tk.Frame(self.root, bg=BG)
        stats.pack(fill='x', padx=22, pady=(0, 10))

        self.stat_widgets = {}
        for key, label in [('total', 'ENLACES'), ('active', 'ACTIVOS'),
                           ('clicks', 'CLICS TOTALES'), ('today', 'CLICS HOY')]:
            box = tk.Frame(stats, bg=SURFACE,
                           highlightbackground=BORDER, highlightthickness=1)
            box.pack(side='left', expand=True, fill='x', padx=(0, 8))

            ib = tk.Frame(box, bg=SURFACE)
            ib.pack(fill='x', padx=16, pady=12)
            tk.Label(ib, text=label, bg=SURFACE, fg=MUTED,
                     font=('Segoe UI', 8, 'bold')).pack(anchor='w')
            val = tk.Label(ib, text="0", bg=SURFACE, fg=TEXT,
                           font=('Segoe UI', 22, 'bold'))
            val.pack(anchor='w', pady=(2, 0))
            self.stat_widgets[key] = val

        # ============== LISTA ==============
        list_frame = tk.Frame(self.root, bg=SURFACE,
                              highlightbackground=BORDER, highlightthickness=1)
        list_frame.pack(fill='both', expand=True, padx=22, pady=(0, 12))

        # Toolbar
        tb = tk.Frame(list_frame, bg=SURFACE)
        tb.pack(fill='x', padx=16, pady=(14, 10))
        tk.Label(tb, text="Panel de gestión", bg=SURFACE, fg=TEXT,
                 font=('Segoe UI', 12, 'bold')).pack(side='left')

        self._tb_button(tb, "📤 Exportar CSV", self.export_csv).pack(side='right', padx=(6, 0))
        self._tb_button(tb, "🔄 Refrescar", self.refresh).pack(side='right')

        # Treeview
        tw = tk.Frame(list_frame, bg=SURFACE)
        tw.pack(fill='both', expand=True, padx=16, pady=(0, 12))

        columns = ('code', 'url', 'clicks', 'status', 'created', 'expires', 'tags')
        self.tree = ttk.Treeview(tw, columns=columns, show='headings',
                                  selectmode='browse')

        heads = {'code': 'Código corto', 'url': 'URL destino', 'clicks': 'Clics',
                 'status': 'Estado', 'created': 'Creado', 'expires': 'Caduca',
                 'tags': 'Etiquetas'}
        widths = {'code': 200, 'url': 340, 'clicks': 60, 'status': 100,
                  'created': 100, 'expires': 100, 'tags': 140}

        for c in columns:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c],
                             anchor='w' if c in ('code', 'url', 'tags') else 'center')

        vsb = ttk.Scrollbar(tw, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        self.tree.bind('<Double-1>', lambda e: self.open_selected())

        # Acciones
        actions = tk.Frame(list_frame, bg=SURFACE)
        actions.pack(fill='x', padx=16, pady=(0, 14))

        self._action_button(actions, "📋 Copiar",   self.copy_selected).pack(side='left', padx=(0, 6))
        self._action_button(actions, "🌐 Abrir",    self.open_selected).pack(side='left', padx=(0, 6))
        self._action_button(actions, "📊 Stats",    self.show_stats).pack(side='left', padx=(0, 6))
        self._action_button(actions, "📱 QR",       self.show_qr).pack(side='left', padx=(0, 6))
        self._action_button(actions, "⏸ Activar/Desactivar",
                            self.toggle_selected).pack(side='left', padx=(0, 6))
        self._action_button(actions, "🗑 Eliminar", self.delete_selected,
                            danger=True).pack(side='right')

        # Status bar
        self.status = tk.Label(self.root, text=f"Servidor: {BASE_URL}",
                                bg=BG, fg=MUTED, font=('Segoe UI', 9),
                                anchor='w')
        self.status.pack(fill='x', padx=26, pady=(0, 10))

    def _labeled_entry(self, parent, label, hint, width=14):
        f = tk.Frame(parent, bg=SURFACE)
        f.pack(side='left', padx=(0, 10))
        txt = label + (f"  ({hint})" if hint else "")
        tk.Label(f, text=txt, bg=SURFACE, fg=MUTED,
                 font=('Segoe UI', 8, 'bold')).pack(anchor='w')
        e = tk.Entry(f, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT,
                     relief='flat', font=('Segoe UI', 10), bd=0, width=width)
        e.pack(ipady=7, pady=(4, 0))
        return e

    def _tb_button(self, parent, text, cmd):
        return tk.Button(parent, text=text, bg=SURFACE_2, fg=TEXT,
                          relief='flat', bd=0, cursor='hand2',
                          font=('Segoe UI', 9), padx=12, pady=6,
                          activebackground=BORDER, activeforeground=TEXT,
                          command=cmd)

    def _action_button(self, parent, text, cmd, danger=False):
        return tk.Button(parent, text=text, bg=SURFACE_2,
                          fg=ERR if danger else TEXT,
                          relief='flat', bd=0, cursor='hand2',
                          font=('Segoe UI', 9), padx=14, pady=7,
                          activebackground=BORDER,
                          activeforeground=ERR if danger else TEXT,
                          command=cmd)

    # ------------------------------------------------------------------
    # CREAR ENLACE
    # ------------------------------------------------------------------
    def create_link(self):
        raw_url = self.entry_url.get().strip()
        code_in = self.entry_code.get().strip()
        pwd_in  = self.entry_pass.get().strip()
        exp_in  = self.entry_exp.get().strip()
        tags_in = self.entry_tags.get().strip()

        # Validar URL
        try:
            url = normalize_url(raw_url)
        except ValueError as e:
            messagebox.showerror("URL no válida", str(e), parent=self.root)
            return

        # Validar fecha
        expires_iso = None
        if exp_in:
            try:
                dt = datetime.strptime(exp_in, '%Y-%m-%d')
                dt = dt.replace(hour=23, minute=59, second=59)
                expires_iso = dt.isoformat(timespec='seconds')
            except ValueError:
                messagebox.showerror("Fecha no válida",
                    "Usa el formato YYYY-MM-DD (ejemplo: 2025-12-31)",
                    parent=self.root)
                return

        conn = get_conn()
        try:
            # Determinar código
            if code_in:
                if not (3 <= len(code_in) <= 32) or \
                   not all(c.isalnum() or c in '-_' for c in code_in):
                    messagebox.showerror("Código no válido",
                        "Debe tener entre 3 y 32 caracteres alfanuméricos, '-' o '_'",
                        parent=self.root)
                    return
                if code_in.lower() in RESERVED:
                    messagebox.showerror("Código reservado",
                        f"'{code_in}' está reservado.", parent=self.root)
                    return
                if conn.execute("SELECT 1 FROM links WHERE code=?",
                                 (code_in,)).fetchone():
                    messagebox.showerror("Código en uso",
                        f"Ya existe un enlace con el código '{code_in}'.",
                        parent=self.root)
                    return
                code = code_in
            else:
                code = unique_code(conn)

            conn.execute(
                "INSERT INTO links (code, url, created_at, active, "
                "pass_hash, expires_at, tags) VALUES (?, ?, ?, 1, ?, ?, ?)",
                (code, url,
                 datetime.now().isoformat(timespec='seconds'),
                 hash_pwd(pwd_in) if pwd_in else None,
                 expires_iso, tags_in)
            )
            conn.commit()
        finally:
            conn.close()

        short = f"{BASE_URL}/{code}"

        # Copiar al portapapeles
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(short)
        except Exception:
            pass

        # Limpiar campos opcionales
        for w in (self.entry_code, self.entry_pass, self.entry_exp, self.entry_tags):
            w.delete(0, 'end')

        # Mensaje
        self.lbl_result.config(text=f"✓  {short}", fg=OK)
        self.root.after(6000, lambda: self.lbl_result.config(text=""))

        self.refresh()
        messagebox.showinfo("Enlace creado",
            f"Enlace corto:\n{short}\n\nDestino:\n{url}\n\n"
            f"(Ya está copiado en el portapapeles)",
            parent=self.root)

    # ------------------------------------------------------------------
    # REFRESCAR LISTA Y STATS
    # ------------------------------------------------------------------
    def refresh(self):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM links ORDER BY created_at DESC"
            ).fetchall()

            total  = len(rows)
            active = sum(1 for r in rows if r['active'])
            clicks = sum(r['total_clicks'] for r in rows)

            today = datetime.now().date().isoformat()
            today_clicks = conn.execute(
                "SELECT COUNT(*) FROM clicks WHERE clicked_at LIKE ?",
                (today + '%',)
            ).fetchone()[0]
        finally:
            conn.close()

        self.stat_widgets['total'].config(text=str(total))
        self.stat_widgets['active'].config(text=str(active))
        self.stat_widgets['clicks'].config(text=str(clicks))
        self.stat_widgets['today'].config(text=str(today_clicks))

        # Rellenar tree
        for it in self.tree.get_children():
            self.tree.delete(it)

        now = datetime.now()
        for r in rows:
            expired = False
            if r['expires_at']:
                try:
                    if now > datetime.fromisoformat(r['expires_at']):
                        expired = True
                except Exception:
                    pass

            if expired:
                status = '⏰ Caducado'
            elif not r['active']:
                status = '⏸ Inactivo'
            else:
                status = '● Activo'

            self.tree.insert('', 'end', iid=r['code'], values=(
                f"{BASE_URL}/{r['code']}",
                r['url'],
                r['total_clicks'],
                status,
                (r['created_at'] or '')[:10],
                (r['expires_at'] or '')[:10] if r['expires_at'] else '—',
                r['tags'] or ''
            ))

        self.status.config(
            text=f"Servidor: {BASE_URL}   ·   {total} enlaces   ·   "
                 f"{active} activos   ·   {clicks} clics totales"
        )

    # ------------------------------------------------------------------
    # ACCIONES
    # ------------------------------------------------------------------
    def _selected_code(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sin selección",
                "Selecciona un enlace de la lista primero.",
                parent=self.root)
            return None
        return sel[0]

    def copy_selected(self):
        code = self._selected_code()
        if not code:
            return
        short = f"{BASE_URL}/{code}"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(short)
            self.status.config(text=f"Copiado al portapapeles: {short}")
        except Exception:
            messagebox.showerror("Error", "No se pudo copiar al portapapeles.",
                                 parent=self.root)

    def open_selected(self):
        code = self._selected_code()
        if not code:
            return
        webbrowser.open(f"{BASE_URL}/{code}")

    def toggle_selected(self):
        code = self._selected_code()
        if not code:
            return
        conn = get_conn()
        try:
            conn.execute("UPDATE links SET active = 1 - active WHERE code=?",
                         (code,))
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def delete_selected(self):
        code = self._selected_code()
        if not code:
            return
        if not messagebox.askyesno("Eliminar enlace",
                f"¿Eliminar '{code}' y todas sus estadísticas?\n\n"
                f"Esta acción no se puede deshacer.",
                parent=self.root):
            return
        conn = get_conn()
        try:
            conn.execute("DELETE FROM links WHERE code=?", (code,))
            conn.execute("DELETE FROM clicks WHERE code=?", (code,))
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    # ------------------------------------------------------------------
    # ESTADÍSTICAS
    # ------------------------------------------------------------------
    def show_stats(self):
        code = self._selected_code()
        if not code:
            return

        conn = get_conn()
        try:
            link = conn.execute("SELECT * FROM links WHERE code=?",
                                 (code,)).fetchone()
            if not link:
                return

            # Clics por día (últimos 7)
            days = []
            for i in range(6, -1, -1):
                d = (datetime.now() - timedelta(days=i)).date()
                start = d.isoformat() + 'T00:00:00'
                end   = d.isoformat() + 'T23:59:59'
                n = conn.execute(
                    "SELECT COUNT(*) FROM clicks WHERE code=? "
                    "AND clicked_at BETWEEN ? AND ?",
                    (code, start, end)
                ).fetchone()[0]
                days.append((d.strftime('%a'), n))

            recent = conn.execute(
                "SELECT * FROM clicks WHERE code=? "
                "ORDER BY clicked_at DESC LIMIT 200",
                (code,)
            ).fetchall()
        finally:
            conn.close()

        # ---- Ventana ----
        win = tk.Toplevel(self.root)
        win.title(f"Estadísticas · {code}")
        win.geometry("860x640")
        win.configure(bg=BG)
        win.transient(self.root)

        # Encabezado
        tk.Label(win, text=f"📊 Analítica · {code}", bg=BG, fg=TEXT,
                 font=('Segoe UI', 15, 'bold')).pack(anchor='w', padx=22, pady=(18, 4))
        tk.Label(win, text=f"Destino: {link['url']}", bg=BG, fg=MUTED,
                 font=('Segoe UI', 9), wraplength=800,
                 justify='left').pack(anchor='w', padx=22)

        # Stats
        srow = tk.Frame(win, bg=BG)
        srow.pack(fill='x', padx=22, pady=(14, 10))
        for label, val in [
            ('CLICS TOTALES', link['total_clicks']),
            ('ESTADO', 'Activo' if link['active'] else 'Inactivo'),
            ('CREADO', (link['created_at'] or '')[:10]),
            ('CADUCA', (link['expires_at'] or '')[:10] if link['expires_at'] else '—'),
        ]:
            b = tk.Frame(srow, bg=SURFACE,
                         highlightbackground=BORDER, highlightthickness=1)
            b.pack(side='left', expand=True, fill='x', padx=(0, 8))
            ib = tk.Frame(b, bg=SURFACE)
            ib.pack(fill='x', padx=14, pady=12)
            tk.Label(ib, text=label, bg=SURFACE, fg=MUTED,
                     font=('Segoe UI', 8, 'bold')).pack(anchor='w')
            tk.Label(ib, text=str(val), bg=SURFACE, fg=TEXT,
                     font=('Segoe UI', 15, 'bold')).pack(anchor='w', pady=(3, 0))

        # Gráfico
        cf = tk.Frame(win, bg=SURFACE,
                      highlightbackground=BORDER, highlightthickness=1)
        cf.pack(fill='x', padx=22, pady=(0, 12))
        tk.Label(cf, text="Clics · últimos 7 días", bg=SURFACE, fg=TEXT,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=16, pady=(14, 6))
        canvas = tk.Canvas(cf, bg=SURFACE, height=150,
                            highlightthickness=0)
        canvas.pack(fill='x', padx=16, pady=(0, 14))

        def draw_chart(_=None):
            canvas.delete('all')
            w = canvas.winfo_width() or 780
            h = 150
            pad = 34
            mx = max((d[1] for d in days), default=0) or 1
            bw = (w - pad * 2) / 7

            for i, (lbl, val) in enumerate(days):
                x = pad + i * bw + bw * 0.15
                bwidth = bw * 0.7
                bh = (val / mx) * (h - 60) if val else 4
                y = h - 24 - bh

                canvas.create_rectangle(x, y, x + bwidth, h - 24,
                                        fill=ACCENT if val else BORDER,
                                        outline='')
                canvas.create_text(x + bwidth / 2, h - 12, text=lbl,
                                   fill=MUTED, font=('Segoe UI', 8))
                if val:
                    canvas.create_text(x + bwidth / 2, y - 9, text=str(val),
                                       fill=TEXT, font=('Segoe UI', 8, 'bold'))

        canvas.bind('<Configure>', draw_chart)
        win.after(120, draw_chart)

        # Tabla
        tf = tk.Frame(win, bg=SURFACE,
                      highlightbackground=BORDER, highlightthickness=1)
        tf.pack(fill='both', expand=True, padx=22, pady=(0, 20))
        tk.Label(tf, text="Últimos clics registrados", bg=SURFACE, fg=TEXT,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=16, pady=(14, 6))

        cols = ('date', 'ip', 'ua', 'ref')
        tree = ttk.Treeview(tf, columns=cols, show='headings', height=9)
        for c, t, wd in [('date', 'Fecha', 150), ('ip', 'IP', 120),
                          ('ua', 'User-Agent', 320), ('ref', 'Referer', 180)]:
            tree.heading(c, text=t)
            tree.column(c, width=wd, anchor='w')

        vsb = ttk.Scrollbar(tf, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True,
                  padx=(16, 0), pady=(0, 16))
        vsb.pack(side='right', fill='y', padx=(0, 16), pady=(0, 16))

        for r in recent:
            dt = (r['clicked_at'] or '')[:19].replace('T', ' ')
            tree.insert('', 'end', values=(
                dt,
                r['ip'] or '—',
                (r['user_agent'] or '—')[:65],
                (r['referer'] or '—')[:55]
            ))

    # ------------------------------------------------------------------
    # CÓDIGO QR
    # ------------------------------------------------------------------
    def show_qr(self):
        code = self._selected_code()
        if not code:
            return

        if not HAS_QR:
            messagebox.showerror("QR no disponible",
                "Falta el módulo 'qrcode'.\n\n"
                "Instálalo con:\n\npip install qrcode[pil]",
                parent=self.root)
            return

        short = f"{BASE_URL}/{code}"

        # Generar la imagen
        try:
            img = qrcode.make(short)
        except Exception as e:
            messagebox.showerror("Error QR",
                f"No se pudo generar el código QR:\n{e}\n\n"
                "Prueba: pip install qrcode[pil]",
                parent=self.root)
            return

        # Guardar temporal para previsualización
        tmp_path = os.path.join(
            os.environ.get('TEMP', os.path.expanduser('~')),
            f'smallurl-{code}.png'
        )
        try:
            img.save(tmp_path)
        except Exception:
            tmp_path = None

        # Ventana
        win = tk.Toplevel(self.root)
        win.title(f"QR · {code}")
        win.geometry("420x600")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)

        tk.Label(win, text="📱 Código QR", bg=BG, fg=TEXT,
                 font=('Segoe UI', 15, 'bold')).pack(pady=(22, 6))
        tk.Label(win, text=short, bg=BG, fg=ACCENT,
                 font=('Consolas', 10, 'bold'),
                 wraplength=360).pack(pady=(0, 16))

        # Vista previa
        if HAS_PIL and tmp_path and os.path.exists(tmp_path):
            try:
                pil_img = Image.open(tmp_path).resize((300, 300), Image.LANCZOS)
                photo = ImageTk.PhotoImage(pil_img)
                lbl = tk.Label(win, image=photo, bg='white',
                               borderwidth=0, highlightthickness=0)
                lbl.image = photo
                lbl.pack(pady=(0, 18))
            except Exception as e:
                tk.Label(win, text=f"(Sin vista previa:\n{e})",
                         bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(pady=(0, 18))
        else:
            tk.Label(win, text="Instala Pillow para la vista previa:\n"
                                "pip install Pillow",
                     bg=BG, fg=WARN, font=('Segoe UI', 9),
                     justify='center').pack(pady=(0, 18))

        # Botones
        bf = tk.Frame(win, bg=BG)
        bf.pack()

        tk.Button(bf, text="📋 Copiar enlace", bg=SURFACE_2, fg=TEXT,
                  relief='flat', bd=0, cursor='hand2', font=('Segoe UI', 9),
                  padx=14, pady=8,
                  activebackground=BORDER, activeforeground=TEXT,
                  command=lambda: (self.root.clipboard_clear(),
                                    self.root.clipboard_append(short),
                                    self.status.config(text=f"Copiado: {short}"))
                  ).pack(side='left', padx=4)

        tk.Button(bf, text="💾 Guardar PNG", bg=ACCENT, fg='white',
                  activebackground=ACCENT_H, activeforeground='white',
                  relief='flat', bd=0, cursor='hand2',
                  font=('Segoe UI', 9, 'bold'), padx=14, pady=8,
                  command=lambda: self._save_qr(img, code)
                  ).pack(side='left', padx=4)

    def _save_qr(self, img, code):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension='.png',
            initialfile=f'smallurl-{code}.png',
            filetypes=[('PNG', '*.png'), ('Todos', '*.*')]
        )
        if path:
            try:
                img.save(path)
                self.status.config(text=f"QR guardado: {path}")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=self.root)

    # ------------------------------------------------------------------
    # EXPORTAR CSV
    # ------------------------------------------------------------------
    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension='.csv',
            initialfile='smallurl-enlaces.csv',
            filetypes=[('CSV', '*.csv'), ('Todos', '*.*')]
        )
        if not path:
            return

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM links ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()

        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(['codigo', 'enlace_corto', 'url_original', 'creado',
                            'caduca', 'activo', 'clics', 'etiquetas'])
                for r in rows:
                    w.writerow([
                        r['code'],
                        f"{BASE_URL}/{r['code']}",
                        r['url'],
                        r['created_at'],
                        r['expires_at'] or '',
                        'si' if r['active'] else 'no',
                        r['total_clicks'],
                        r['tags'] or ''
                    ])
        except Exception as e:
            messagebox.showerror("Error al exportar", str(e), parent=self.root)
            return

        self.status.config(text=f"CSV exportado: {path}")
        messagebox.showinfo("Exportación completa",
            f"{len(rows)} enlaces exportados a:\n{path}",
            parent=self.root)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()
    app = SmallURLApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
