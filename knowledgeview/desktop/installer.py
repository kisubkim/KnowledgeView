"""Self-install for the PyInstaller build (per-user, no admin rights).

Running KnowledgeView-Setup.exe from anywhere opens an install window: install folder,
data folder (settings, cache, uploads, logs), output folder (HTML pages), an optional
source MD folder, port, remote access + admin token, shortcuts and sign-in start.
Installing copies the exe to ``<install>\\KnowledgeView.exe``, writes
``<install>\\knowledgeview.json`` (data_dir, host, port, admin_token), seeds
``<data>\\settings.json`` (output_dir, source_roots), registers an Apps & Features entry
and launches the installed copy. ``--uninstall`` reverses it.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

from .. import __version__, claude_check
from ..config import DEFAULT_PORT, INSTALL_CONFIG_NAME
from . import single_instance
from .app import IDYES, MB_ICONWARN, MB_YESNO, fresh_env, message

log = logging.getLogger(__name__)

APP_NAME = "KnowledgeView"
EXE_NAME = "KnowledgeView.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\KnowledgeView"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))


def default_install_dir() -> Path:
    return _local_appdata() / "Programs" / APP_NAME


def registered_install_dir() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def _documents() -> Path:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0]))
    except OSError:
        return Path.home() / "Documents"


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def needs_install() -> bool:
    """True when the frozen exe runs from somewhere other than an install folder."""
    if not is_frozen():
        return False
    here = Path(sys.executable).resolve()
    return not (here.name.lower() == EXE_NAME.lower() and (here.parent / INSTALL_CONFIG_NAME).is_file())


def _start_menu_lnk() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def _desktop_lnk() -> Path:
    return Path.home() / "Desktop" / f"{APP_NAME}.lnk"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_shortcut(lnk: Path, target: Path) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))});"
        f"$s.TargetPath={_ps_quote(str(target))};"
        f"$s.WorkingDirectory={_ps_quote(str(target.parent))};"
        f"$s.Description='Markdown 문서를 상세화한 HTML 페이지로 만들고 보는 앱';$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, creationflags=CREATE_NO_WINDOW, timeout=30)


def set_autostart(command: str | None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if command:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def _register_uninstall(exe: Path) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in {
            "DisplayName": APP_NAME,
            "DisplayVersion": __version__,
            "DisplayIcon": str(exe),
            "InstallLocation": str(exe.parent),
            "UninstallString": f'"{exe}" --uninstall',
            "Publisher": APP_NAME,
        }.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def current_values() -> dict:
    """Defaults for the install window; an existing install pre-fills its own values."""
    inst = registered_install_dir() or default_install_dir()
    cfg = _read_json(inst / INSTALL_CONFIG_NAME)
    data = Path(cfg.get("data_dir") or (_local_appdata() / APP_NAME / "data"))
    st = _read_json(data / "settings.json")
    out = st.get("output_dir") or str(_documents() / APP_NAME / "output")
    roots = st.get("source_roots") or []
    return {
        "install_dir": str(inst),
        "data_dir": str(data),
        "output_dir": str(out if Path(out).is_absolute() else data.parent / out),
        "source_dir": roots[0] if roots else "",
        "port": int(cfg.get("port") or DEFAULT_PORT),
        "remote": cfg.get("host") == "0.0.0.0",
        "admin_token": cfg.get("admin_token") or "",
        "start_menu": True,
        "desktop": not cfg or _desktop_lnk().exists(),
        "autostart": autostart_enabled(),
        "upgrade": bool(cfg),
    }


def install(v: dict) -> Path:
    """Do the install with the values from the window. Returns the installed exe."""
    if not single_instance.stop_running_instance():
        raise RuntimeError("실행 중인 KnowledgeView를 종료하지 못했습니다. 트레이에서 종료한 뒤 다시 실행하세요.")
    inst, data, out = Path(v["install_dir"]), Path(v["data_dir"]), Path(v["output_dir"])
    for d in (inst, data, out):
        d.mkdir(parents=True, exist_ok=True)
    target = inst / EXE_NAME
    if Path(sys.executable).resolve() != target.resolve():
        for _ in range(20):
            try:
                shutil.copy2(sys.executable, target)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            raise RuntimeError("실행 파일을 복사하지 못했습니다. 잠시 후 다시 시도하세요.")

    cfg = {"data_dir": str(data), "host": "0.0.0.0" if v["remote"] else "127.0.0.1",
           "port": int(v["port"]), "admin_token": v["admin_token"].strip() if v["remote"] else ""}
    (inst / INSTALL_CONFIG_NAME).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # Seed the app settings with the folders chosen here (other settings are kept).
    st_path = data / "settings.json"
    st = _read_json(st_path)
    st["output_dir"] = str(out)
    src = v.get("source_dir", "").strip()
    roots = [r for r in st.get("source_roots", []) if r]
    if src and src not in roots:
        roots.insert(0, src)
    st["source_roots"] = roots
    st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    for enabled, lnk in ((v["start_menu"], _start_menu_lnk()), (v["desktop"], _desktop_lnk())):
        try:
            if enabled:
                _create_shortcut(lnk, target)
            elif lnk.exists():
                lnk.unlink()
        except (OSError, subprocess.SubprocessError):
            log.exception("shortcut failed: %s", lnk)
    set_autostart(f'"{target}" --tray' if v["autostart"] else None)
    _register_uninstall(target)
    return target


def launch(exe: Path) -> None:
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=DETACHED_PROCESS, close_fds=True, env=fresh_env())


def uninstall() -> None:
    if message("KnowledgeView를 제거할까요?", MB_YESNO | MB_ICONWARN) != IDYES:
        return
    inst = Path(sys.executable).resolve().parent if is_frozen() else (registered_install_dir() or default_install_dir())
    cfg = _read_json(inst / INSTALL_CONFIG_NAME)
    data = Path(cfg["data_dir"]) if cfg.get("data_dir") else None
    out = None
    if data:
        o = _read_json(data / "settings.json").get("output_dir")
        out = Path(o) if o and Path(o).is_absolute() else None
    single_instance.stop_running_instance()
    set_autostart(None)
    for lnk in (_start_menu_lnk(), _desktop_lnk()):
        try:
            lnk.unlink()
        except FileNotFoundError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    removed_data = False
    if data and data.exists():
        detail = f"데이터 폴더: {data}" + (f"\n출력 폴더(만든 HTML 페이지): {out}" if out else "")
        if message("설정, 캐시, 만든 HTML 페이지도 함께 삭제할까요?\n\n" + detail +
                   "\n\n[아니요]를 고르면 그대로 남겨 두고, 다시 설치할 때 이어서 쓸 수 있습니다.",
                   MB_YESNO | MB_ICONWARN) == IDYES:
            for d in filter(None, (data, out)):
                shutil.rmtree(d, ignore_errors=True)
            removed_data = True
    # The running exe can't delete itself; a detached cmd removes the folder after we exit.
    if inst.exists() and (inst / EXE_NAME).exists():
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 4 >nul & rmdir /s /q "{inst}"',
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
    message("제거되었습니다." + ("" if removed_data or not data else f"\n(데이터와 페이지는 남아 있습니다: {data})"))


# --------------------------------------------------------------------------- install window


def run_installer() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    v = current_values()
    root = tk.Tk()
    root.title(f"{APP_NAME} {__version__} 설치")
    root.resizable(False, False)
    try:
        from PIL import ImageTk

        from .icon import render_icon
        root._icon = ImageTk.PhotoImage(render_icon(64))  # keep a reference
        root.iconphoto(True, root._icon)
    except Exception:  # noqa: BLE001 - the icon is cosmetic
        pass
    style = ttk.Style(root)
    for font_name in ("Malgun Gothic", "맑은 고딕"):
        try:
            style.configure(".", font=(font_name, 10))
            break
        except tk.TclError:
            continue

    frm = ttk.Frame(root, padding=18)
    frm.grid(sticky="nsew")
    title = "설치된 KnowledgeView를 업데이트합니다." if v["upgrade"] else "KnowledgeView를 설치합니다."
    ttk.Label(frm, text=title, font=("Malgun Gothic", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(frm, text="관리자 권한 없이 현재 사용자에게만 설치됩니다.", foreground="#5d6676").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(2, 12))

    vars_: dict[str, tk.Variable] = {}
    row = 2

    def folder_row(key: str, label: str, hint: str) -> None:
        nonlocal row
        vars_[key] = tk.StringVar(value=v[key])
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
        ent = ttk.Entry(frm, textvariable=vars_[key], width=62)
        ent.grid(row=row, column=1, sticky="we", pady=(6, 0), padx=(8, 6))

        def browse() -> None:
            start = vars_[key].get() or str(Path.home())
            d = filedialog.askdirectory(parent=root, initialdir=start if Path(start).exists() else str(Path.home()),
                                        title=label)
            if d:
                vars_[key].set(str(Path(d)))
        ttk.Button(frm, text="찾아보기", command=browse).grid(row=row, column=2, pady=(6, 0))
        row += 1
        ttk.Label(frm, text=hint, foreground="#5d6676").grid(row=row, column=1, columnspan=2, sticky="w", padx=(8, 0))
        row += 1

    folder_row("install_dir", "설치 위치", "실행 파일과 설치 설정(knowledgeview.json)이 들어갑니다.")
    folder_row("data_dir", "데이터 폴더", "설정, LLM 캐시, 업로드한 MD, 로그가 저장됩니다.")
    folder_row("output_dir", "출력 폴더", "만든 HTML 페이지가 카테고리별 하위 폴더로 저장됩니다.")
    folder_row("source_dir", "원본 MD 폴더 (선택)", "나중에 웹 화면의 설정에서 추가·변경할 수 있습니다. 읽기만 합니다.")

    sep = ttk.Separator(frm)
    sep.grid(row=row, column=0, columnspan=3, sticky="we", pady=12)
    row += 1

    vars_["port"] = tk.StringVar(value=str(v["port"]))
    ttk.Label(frm, text="포트").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=vars_["port"], width=8).grid(row=row, column=1, sticky="w", padx=(8, 0))
    row += 1

    vars_["remote"] = tk.BooleanVar(value=v["remote"])
    vars_["admin_token"] = tk.StringVar(value=v["admin_token"])
    token_entry = ttk.Entry(frm, textvariable=vars_["admin_token"], width=40)

    def toggle_remote() -> None:
        on = vars_["remote"].get()
        token_entry.configure(state="normal" if on else "disabled")
        if on and not vars_["admin_token"].get():
            vars_["admin_token"].set(secrets.token_urlsafe(12))

    ttk.Checkbutton(frm, text="다른 PC에서 웹으로 접속 허용 (0.0.0.0, Windows 방화벽 허용 창이 뜰 수 있음)",
                    variable=vars_["remote"], command=toggle_remote).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
    row += 1
    ttk.Label(frm, text="관리 토큰").grid(row=row, column=0, sticky="w")
    token_entry.grid(row=row, column=1, sticky="w", padx=(8, 0))
    row += 1
    ttk.Label(frm, text="다른 PC에서 생성·삭제·설정을 하려면 이 토큰이 필요합니다(페이지 보기는 토큰 없이 가능).",
              foreground="#5d6676").grid(row=row, column=1, columnspan=2, sticky="w", padx=(8, 0))
    row += 1
    toggle_remote()
    if not v["remote"]:
        token_entry.configure(state="disabled")

    for key, text in (("start_menu", "시작 메뉴에 바로가기 만들기"), ("desktop", "바탕 화면에 바로가기 만들기"),
                      ("autostart", "Windows 로그인 시 자동 실행 (트레이에서 대기)")):
        vars_[key] = tk.BooleanVar(value=v[key])
        ttk.Checkbutton(frm, text=text, variable=vars_[key]).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        row += 1

    sep2 = ttk.Separator(frm)
    sep2.grid(row=row, column=0, columnspan=3, sticky="we", pady=12)
    row += 1
    claude_var = tk.StringVar(value="Claude Code 확인 중…")
    ttk.Label(frm, textvariable=claude_var, wraplength=620, justify="left").grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1

    def check_claude() -> None:
        st = claude_check.status(force=True)
        if claude_check.usable(st):
            sub = f" ({st['subscription']})" if st.get("subscription") else ""
            text = f"Claude Code: 설치됨, 로그인됨{sub}. 'Claude Code CLI 상세화' 방식을 쓸 수 있습니다."
        elif st.get("installed"):
            text = "Claude Code: 설치됨, 로그인 안 됨. 터미널에서 claude 로 로그인하면 'Claude Code CLI 상세화' 방식이 나타납니다."
        else:
            text = "Claude Code: 설치 안 됨. 'Claude Code CLI 상세화' 방식은 표시되지 않습니다(구조화, OpenAI 호환 방식은 사용 가능)."
        root.after(0, claude_var.set, text)

    threading.Thread(target=check_claude, daemon=True).start()

    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(16, 0))
    status_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status_var, foreground="#2f6fde").grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def collect() -> dict | None:
        vals = {k: var.get() for k, var in vars_.items()}
        for key, label in (("install_dir", "설치 위치"), ("data_dir", "데이터 폴더"), ("output_dir", "출력 폴더")):
            p = str(vals[key]).strip()
            if not p or not Path(p).is_absolute():
                messagebox.showwarning(APP_NAME, f"{label}를 절대 경로로 지정하세요.", parent=root)
                return None
            vals[key] = p
        if vals["source_dir"].strip() and not Path(vals["source_dir"].strip()).is_dir():
            if not messagebox.askyesno(APP_NAME, "원본 MD 폴더가 없습니다. 그대로 진행할까요?", parent=root):
                return None
        try:
            port = int(str(vals["port"]).strip())
            assert 1024 <= port <= 65535
        except (ValueError, AssertionError):
            messagebox.showwarning(APP_NAME, "포트는 1024~65535 사이 숫자여야 합니다.", parent=root)
            return None
        vals["port"] = port
        if _port_in_use(port) and not (single_instance.is_running() and port == v["port"]):
            if not messagebox.askyesno(APP_NAME, f"포트 {port}를 다른 프로그램이 쓰고 있습니다. 그대로 두면 KnowledgeView가 시작하지 못합니다.\n"
                                                 "그래도 진행할까요? (설치 후 웹 화면의 설정 > 서버에서 바꿀 수 있습니다)", parent=root):
                return None
        if vals["remote"] and not str(vals["admin_token"]).strip():
            if not messagebox.askyesno(APP_NAME, "관리 토큰 없이 다른 PC 접속을 허용하면, 접속한 누구나 생성·삭제·설정과 "
                                                 "Claude 호출을 할 수 있습니다. 그대로 진행할까요?", parent=root):
                return None
        return vals

    def do_install() -> None:
        vals = collect()
        if not vals:
            return
        for b in btns.winfo_children():
            b.configure(state="disabled")
        status_var.set("설치 중…")

        def work() -> None:
            try:
                exe = install(vals)
            except Exception as e:  # noqa: BLE001 - shown to the user
                log.exception("install failed")
                root.after(0, lambda: (messagebox.showerror(APP_NAME, f"설치하지 못했습니다.\n\n{e}", parent=root),
                                       status_var.set(""), [b.configure(state="normal") for b in btns.winfo_children()]))
                return

            def done() -> None:
                launch(exe)
                messagebox.showinfo(APP_NAME, "설치가 완료되었습니다.\n\n브라우저에서 KnowledgeView가 열립니다. "
                                              "작업 표시줄 트레이의 K 아이콘에서 다시 열거나 종료할 수 있습니다.\n"
                                              "(아이콘이 숨겨져 있으면 ^ 버튼 안에 있습니다)", parent=root)
                root.destroy()
            root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    ttk.Button(btns, text="취소", command=root.destroy).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text="업데이트" if v["upgrade"] else "설치", command=do_install).pack(side="right")
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{max(0, (root.winfo_screenheight() - h) // 3)}")
    root.mainloop()
