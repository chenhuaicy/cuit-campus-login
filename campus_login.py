"""CUIT campus portal assistant. No embedded credentials. Windows / Edge."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import re
import sys
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlparse
import urllib.request
import winreg

PORTAL = 'http://10.254.241.66/portal/entry/pc/authenticate;flowParams=undefined;from='
HOST = '10.254.241.66'
DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CUIT-AutoLogin'
CONFIG = DATA / 'settings.json'
CARRIERS = ('中国电信', '中国移动', '中国联通', '校园网')
CLOSE_ACTIONS = ('最小化到右下角托盘（继续运行）', '退出程序（停止监测）')
SUCCESS = re.compile(r'已连接网络|网络连接成功|认证成功|上网成功|您已成功登录')
FAILURE = re.compile(r'密码错误|密码不正确|账号或密码|账户或密码|认证失败|账号不存在|用户不存在|余额不足|账号已停用')


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(raw, decrypt=False):
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise OSError('Windows 无法解密本机密码，请重新填写。')
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def load_settings():
    if not CONFIG.exists():
        return {}
    obj = json.loads(CONFIG.read_text(encoding='utf-8'))
    if obj.get('secret'):
        obj['password'] = crypt(base64.b64decode(obj['secret']), True).decode('utf-8')
    return obj


def save_settings(obj):
    obj = dict(obj)
    password = obj.pop('password', '')
    obj['secret'] = base64.b64encode(crypt(password.encode())).decode() if obj['remember'] else ''
    DATA.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG)


def startup(enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            if getattr(sys, 'frozen', False):
                command = f'"{sys.executable}" --auto'
            else:
                command = f'"{sys.executable}" "{Path(__file__).resolve()}" --auto'
            winreg.SetValueEx(key, 'CUITAutoLogin', 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, 'CUITAutoLogin')
            except FileNotFoundError:
                pass


def internet_ok():
    # Bypass environment proxies; require the exact known body, not merely HTTP 200.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url, expected in [('http://www.msftconnecttest.com/connecttest.txt', b'Microsoft Connect Test'),
                          ('http://www.msftncsi.com/ncsi.txt', b'Microsoft NCSI')]:
        try:
            with opener.open(url, timeout=4) as response:
                if response.status == 200 and response.read(256).strip() == expected:
                    return True
        except Exception:
            pass
    return False


def confirm_offline(stop, report, probe=None):
    """First failed round already observed; require two spaced failed rechecks."""
    probe = probe or internet_ok
    for round_number in (2, 3):
        report(f'暂未确认联网，5 秒后复查（第 {round_number}/3 轮）；暂不打开认证页。')
        if stop.wait(5):
            return None
        if probe():
            return False
    return True


def trusted(frame):
    u = urlparse(frame.url)
    return u.scheme in ('http', 'https') and u.hostname == HOST


def portal_tabs(mode, candidates=None):
    """Inspect/close only a previously identified active Edge portal tab via UI Automation."""
    script = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'close_portal_tab.ps1'
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-STA',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Mode', mode],
                                input=json.dumps(candidates or []), capture_output=True,
                                encoding='utf-8', errors='replace', timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        parsed = json.loads(result.stdout) if result.returncode == 0 else []
        return parsed if isinstance(parsed, list) else []
    except Exception:
        # A minimized or inaccessible browser must not interrupt network monitoring.
        return []


def close_external_tabs(candidates, report):
    if not candidates:
        return []
    closed = portal_tabs('Close', candidates)
    if closed:
        report('已关闭系统弹出的校园网认证标签页，其他网页保持打开。')
    return [item for item in candidates if item not in closed]


def remember_portal_tabs(candidates):
    for tab in portal_tabs('Scan'):
        if tab not in candidates and len(candidates) < 8:
            candidates.append(tab)
    return candidates


def visible_first(locator):
    for i in range(min(locator.count(), 30)):
        item = locator.nth(i)
        if item.is_visible():
            return item
    return None


def carrier_control(frame, carrier):
    short = carrier.replace('中国', '')
    pattern = re.compile(r'^\s*(?:中国)?' + re.escape(short) + r'(?:上网|网络|认证)?\s*$')
    for role in ('button', 'link', 'radio'):
        hit = visible_first(frame.get_by_role(role, name=pattern))
        if hit is not None:
            return hit
    return visible_first(frame.get_by_text(pattern))


def authenticate(page, options, stop, report, timeout=65, probe=None):
    """Operate only the pinned campus host, including its embedded frames."""
    deadline = time.monotonic() + timeout
    submitted = False
    selected = False
    confirmed = False
    probe = probe or internet_ok
    next_probe = 0
    while time.monotonic() < deadline and not stop.is_set():
        if page.is_closed():
            return 'browser_closed'
        if (submitted or selected) and time.monotonic() >= next_probe:
            if probe():
                return 'online'
            next_probe = time.monotonic() + 3
        for frame in page.frames:
            if not trusted(frame):
                continue
            try:
                body = frame.locator('body').inner_text(timeout=1200)
                if SUCCESS.search(body):
                    return 'success'
                if FAILURE.search(body):
                    report('页面提示认证失败。请核对账号、密码、套餐状态；已停止自动重试。')
                    return 'rejected'
                if not submitted:
                    name = visible_first(frame.locator('#nameInput, input[placeholder="请输入账号"]'))
                    pwd = visible_first(frame.locator('input[placeholder="请输入密码"], input[type="password"]'))
                    if name is not None and pwd is not None:
                        name.fill(options['username'])
                        pwd.fill(options['password'])
                        if options['consent']:
                            for cb in frame.get_by_role('checkbox').all():
                                if cb.is_visible() and not cb.is_checked():
                                    cb.check(timeout=2000)
                        button = visible_first(frame.locator('#submitBtn'))
                        if button is None:
                            button = visible_first(frame.get_by_role('button', name='立即登录', exact=True))
                        if button is not None:
                            button.click(timeout=4000)
                            submitted = True
                            report('已提交校园网登录，等待运营商页面…')
                            break
                if not selected:
                    hit = carrier_control(frame, options['carrier'])
                    if hit is not None:
                        hit.click(timeout=4000)
                        selected = True
                        report('已选择' + options['carrier'] + '，检查服务确认按钮…')
                        break
                if selected and not confirmed and (
                    '请选择服务' in body or '/serviceSelection' in urlparse(frame.url).path
                ):
                    confirm = visible_first(frame.get_by_role('button', name=re.compile(r'^\s*(?:确定|确认)\s*$')))
                    if confirm is not None and confirm.is_enabled():
                        if stop.is_set():
                            return 'cancelled'
                        confirm.click(timeout=4000)
                        confirmed = True
                        report('已点击服务确认，等待网络连接结果…')
                        break
            except Exception:
                # Portal navigation detaches the old frame. Reinspect the new one.
                continue
        try:
            page.wait_for_timeout(500)
        except Exception:
            if page.is_closed():
                return 'browser_closed'
            raise
    return 'cancelled' if stop.is_set() else 'unrecognized'


def login_session(pw, options, stop, report, probe=None):
    """Own only the temporary login browser; closing it never stops the monitor."""
    probe = probe or internet_ok
    browser = pw.chromium.launch(channel='msedge', headless=not options['show'],
                                 args=['--no-proxy-server'])
    page = None
    try:
        page = browser.new_page()
        page.set_default_timeout(4000)
        try:
            page.goto(PORTAL, wait_until='domcontentloaded', timeout=20000)
        except Exception:
            return 'browser_closed' if page.is_closed() or not browser.is_connected() else 'portal_unavailable'
        result = authenticate(page, options, stop, report, probe=probe)
        if result == 'unrecognized' and options['show']:
            report('尚未确认联网，保留登录窗口供查看；联网后会自动关闭，手动关窗后仍继续监测。')
            while not stop.is_set():
                if probe():
                    return 'online'
                if page.is_closed():
                    return 'browser_closed'
                page.wait_for_timeout(500)
                if stop.wait(2.5):
                    break
            return 'cancelled'
        return result
    except Exception:
        if (page is not None and page.is_closed()) or not browser.is_connected():
            return 'browser_closed'
        raise
    finally:
        try:
            browser.close()
        except Exception:
            pass


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.locked = False
        self.closing = False
        self.ui_actions = queue.Queue()
        self.tray = None
        self.tray_ready = threading.Event()
        scale = root.winfo_fpixels('1i') / 96
        root.title('成信大校园网助手 · 航空港')
        icon = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'campus-icon.ico'
        if icon.exists():
            root.iconbitmap(str(icon))
        root.geometry(f'{int(570*scale)}x{int(660*scale)}')
        root.minsize(int(540*scale), int(620*scale))
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='校园网自动登录', font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='成都信息工程大学 · 航空港校区 · 非官方工具').pack(anchor='w', pady=(3, 18))
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.carrier = tk.StringVar(value=CARRIERS[0])
        self.remember = tk.BooleanVar(value=False)
        self.monitor = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=False)
        self.consent = tk.BooleanVar(value=False)
        self.show = tk.BooleanVar(value=True)
        self.close_action = tk.StringVar(value=CLOSE_ACTIONS[0])
        self.fields = []
        for label, variable in [('校园网账号', self.username), ('校园网密码', self.password)]:
            ttk.Label(frame, text=label).pack(anchor='w')
            field = ttk.Entry(frame, textvariable=variable, show='•' if variable is self.password else '')
            field.pack(fill='x', pady=(4, 10))
            self.fields.append(field)
        ttk.Label(frame, text='运营商（须与你的校园网套餐一致）').pack(anchor='w')
        box = ttk.Combobox(frame, textvariable=self.carrier, values=CARRIERS, state='readonly')
        box.pack(fill='x', pady=(4, 12))
        self.fields.append(box)
        for label, variable in [('记住密码（仅当前 Windows 用户可解密）', self.remember),
                                ('掉线后自动重连（每 30 秒检查）', self.monitor),
                                ('Windows 登录后自动运行（需记住密码）', self.autostart),
                                ('显示登录浏览器（首次使用建议勾选）', self.show),
                                ('我已阅读并同意校园网页面的免责声明及隐私协议', self.consent)]:
            cb = ttk.Checkbutton(frame, text=label, variable=variable)
            cb.pack(anchor='w', pady=2)
            self.fields.append(cb)
        close_row = ttk.Frame(frame)
        close_row.pack(fill='x', pady=(10, 0))
        ttk.Label(close_row, text='点击 × 时：').pack(side='left')
        close_box = ttk.Combobox(close_row, textvariable=self.close_action,
                                values=CLOSE_ACTIONS, state='readonly', width=29)
        close_box.pack(side='left', padx=(8, 0))
        close_box.bind('<<ComboboxSelected>>', self.save_close_action)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(16, 10))
        self.start_button = ttk.Button(buttons, text='保存并开始', command=self.start)
        self.start_button.pack(side='left')
        ttk.Button(buttons, text='停止', command=self.stop.set).pack(side='left', padx=8)
        ttk.Button(buttons, text='退出程序', command=self.exit_app).pack(side='left')
        ttk.Button(buttons, text='清除保存信息', command=self.clear).pack(side='right')
        self.status = tk.StringVar(value='请填写账号并选择运营商。首次运行会打开独立的 Edge 窗口。')
        ttk.Label(frame, textvariable=self.status, wraplength=int(510*scale), foreground='#12659b').pack(anchor='w', pady=4)
        self.log = tk.Text(frame, height=5, font=('Microsoft YaHei UI', 9), state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True, pady=(8, 0))
        try:
            saved = load_settings()
            for name in ('username', 'password', 'carrier', 'remember', 'monitor', 'autostart', 'consent', 'show', 'close_action'):
                if name in saved:
                    getattr(self, name).set(saved[name])
            if self.close_action.get() not in CLOSE_ACTIONS:
                self.close_action.set(CLOSE_ACTIONS[0])
        except Exception:
            self.status.set('保存的信息无法读取，请重新填写账号密码。')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Unmap>', self.on_unmap)
        self.start_tray(icon)
        root.after(150, self.poll)
        if '--auto' in sys.argv and self.password.get() and self.consent.get():
            root.after(1000, self.start)
            root.after(500, self.minimize_to_tray)

    def start_tray(self, icon_path):
        try:
            import pystray
            from PIL import Image
            self.tray = pystray.Icon('CUITAutoLogin', Image.open(icon_path).copy(),
                '成信大校园网助手', menu=pystray.Menu(
                    pystray.MenuItem('显示窗口', lambda icon, item: self.ui_actions.put('show'), default=True),
                    pystray.MenuItem('退出程序', lambda icon, item: self.ui_actions.put('exit'))))
            def setup(icon):
                try:
                    icon.visible = True
                    self.tray_ready.set()
                except Exception:
                    self.ui_actions.put('tray_failed')
            self.tray.run_detached(setup=setup)
        except Exception:
            self.report('托盘图标未能创建，暂时保留任务栏窗口。')

    def minimize_to_tray(self):
        if self.closing:
            return
        if self.tray_ready.is_set():
            self.root.withdraw()
        else:
            self.report('托盘尚未就绪，暂时保留任务栏窗口。')

    def on_unmap(self, event):
        if event.widget is self.root and self.root.state() == 'iconic':
            self.root.after(50, self.minimize_to_tray)

    def show_window(self):
        self.root.deiconify()
        self.root.geometry('+160+100')
        self.root.lift()
        self.root.focus_force()

    def report(self, text):
        self.events.put(text)

    def save_close_action(self, event=None):
        try:
            # Update only this preference, preserving encrypted credentials and startup settings.
            obj = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
            obj['close_action'] = self.close_action.get()
            DATA.mkdir(parents=True, exist_ok=True)
            temp = CONFIG.with_suffix('.tmp')
            temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(CONFIG)
            self.report('关闭按钮设置已保存：' + self.close_action.get())
        except Exception:
            messagebox.showerror('保存失败', '本次选择已生效，但无法记住设置，请检查配置文件权限。')

    def poll(self):
        while not self.ui_actions.empty():
            action = self.ui_actions.get()
            if action == 'show' and not self.closing:
                self.show_window()
            elif action == 'exit':
                self.exit_app()
            elif action == 'tray_failed':
                self.tray_ready.clear()
                self.show_window()
                self.report('托盘不可用，已恢复窗口。')
        while not self.events.empty():
            msg = self.events.get()
            self.status.set(msg)
            self.log.configure(state='normal')
            self.log.insert('end', time.strftime('%H:%M:%S ') + msg + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
        running = self.worker is not None and self.worker.is_alive()
        if running != self.locked:
            for widget in self.fields:
                widget.configure(state='disabled' if running else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
            self.locked = running
        self.start_button.configure(state='disabled' if running else 'normal')
        if self.closing and not running:
            if self.tray is not None:
                self.tray.stop()
            self.root.destroy()
            return
        self.root.after(200, self.poll)

    def clear(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('请先停止', '请先点击停止，等运行结束后再清除。')
            return
        try:
            CONFIG.unlink(missing_ok=True)
            startup(False)
            self.username.set('')
            self.password.set('')
            self.autostart.set(False)
            self.remember.set(False)
            self.close_action.set(CLOSE_ACTIONS[0])
            self.report('已清除本机保存的信息和自动启动设置。')
        except Exception:
            messagebox.showerror('清除失败', '无法清除本机配置，请检查文件权限。')

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        options = {name: getattr(self, name).get() for name in
                   ('username', 'password', 'carrier', 'remember', 'monitor', 'autostart', 'consent', 'show', 'close_action')}
        options['username'] = options['username'].strip()
        if not options['username'] or not options['password']:
            messagebox.showinfo('填写信息', '请填写你自己的校园网账号和密码。')
            return
        if not options['consent']:
            messagebox.showinfo('学校协议', '请先在校园网页面阅读协议，再勾选同意。')
            return
        if options['autostart'] and not options['remember']:
            messagebox.showinfo('自动启动', '自动启动需要勾选“记住密码”。')
            return
        try:
            save_settings(options)
            startup(options['autostart'])
        except Exception:
            messagebox.showerror('保存失败', '无法保存配置或设置自动启动，请检查文件权限。')
            return
        self.stop.clear()
        self.worker = threading.Thread(target=self.run, args=(options,), daemon=True)
        self.worker.start()
        self.start_button.configure(state='disabled')

    def run(self, options):
        from playwright.sync_api import sync_playwright
        pending_tabs = portal_tabs('Scan')
        cleanup_until = time.monotonic() + 120
        try:
            while not self.stop.is_set():
                if internet_ok():
                    if time.monotonic() < cleanup_until:
                        pending_tabs = remember_portal_tabs(pending_tabs)
                    pending_tabs = close_external_tabs(pending_tabs, self.report)
                    self.report('外网检测正常。' + ('继续监测连接。' if options['monitor'] else ''))
                else:
                    confirmed = confirm_offline(self.stop, self.report)
                    if confirmed is None:
                        break
                    if not confirmed:
                        self.report('复查已恢复联网，已跳过认证，继续监测。')
                        if not options['monitor'] or self.stop.wait(30):
                            break
                        continue
                    cleanup_until = time.monotonic() + 120
                    pending_tabs = remember_portal_tabs(pending_tabs)
                    self.report('连续 3 轮联网检测未通过，检查校园网认证页面…')
                    with sync_playwright() as pw:
                        result = login_session(pw, options, self.stop, self.report)
                    if result in ('cancelled', 'rejected'):
                        break
                    if result == 'unrecognized':
                        self.report('未识别到认证结果，已暂停自动提交；请打开显示登录浏览器后检查页面。')
                        break
                    if result == 'browser_closed':
                        self.report('登录浏览器已关闭。' + ('后台监测继续运行，稍后重新检查网络。' if options['monitor'] else '本次登录已结束。'))
                    elif result == 'portal_unavailable':
                        self.report('校园网入口暂时无法打开，稍后重新检查。')
                    elif result == 'online' or internet_ok():
                        cleanup_until = time.monotonic() + 120
                        pending_tabs = remember_portal_tabs(pending_tabs)
                        pending_tabs = close_external_tabs(pending_tabs, self.report)
                        self.report('外网检测通过，已自动关闭登录浏览器。')
                    else:
                        self.report('校园网页面显示已连接，已关闭登录浏览器；继续监测外网。')
                if not options['monitor'] or self.stop.wait(30):
                    break
        except Exception:
            # Never expose Playwright call logs: fill() diagnostics can include passwords.
            self.report('运行中断：请确认已连接校园网且安装了 Microsoft Edge，然后重新开始。')
        finally:
            if self.stop.is_set():
                self.report('任务已停止；可修改设置后再次开始。')

    def close(self):
        if self.close_action.get() == CLOSE_ACTIONS[0] and not self.closing:
            self.minimize_to_tray()
            return
        self.exit_app()

    def exit_app(self):
        self.closing = True
        self.stop.set()
        self.report('正在停止并关闭登录窗口…')


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CUIT.CampusAutoLogin.Assistant')
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        try:
            from playwright.sync_api import sync_playwright
            assert crypt(crypt(b'local-test'), True) == b'local-test'
            testroot = tk.Tk()
            testroot.withdraw()
            testroot.destroy()
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page()
                page.set_content('<title>CUIT test</title>')
                assert page.title() == 'CUIT test'
                browser.close()
            Path(sys.argv[2]).write_text('PASS: packaged Tk, DPAPI, Playwright and installed Edge', encoding='utf-8')
        except Exception as exc:
            Path(sys.argv[2]).write_text('FAIL: ' + type(exc).__name__, encoding='utf-8')
        return
    # Per-user session mutex prevents duplicate background logins.
    ctypes.windll.kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\CUITAutoLoginAssistant')
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(None, '程序已经运行，请在右下角托盘（或小箭头内）点击校园网图标显示窗口。', '校园网助手', 0)
        return
    root = tk.Tk()
    App(root)
    root.mainloop()
    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.kernel32.CloseHandle(handle)


if __name__ == '__main__':
    main()
