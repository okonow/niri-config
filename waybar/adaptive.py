#!/usr/bin/env python3
"""Подгоняет цвет waybar под то, что находится прямо под панелью.

Как работает:
  1. Слушает события niri (`niri msg --json event-stream`).
  2. После смены окна/стола ждёт, пока уляжется анимация, и делает снимок
     тонкой полоски экрана сразу под панелью (через grim).
  3. Если на столе есть окно - панель получает его цвет.
     Если стол пустой - панель становится прозрачной (видны обои),
     а цвет текста выбирается по яркости обоев.
  4. Пишет style.css = цвета + style-base.css. Waybar подхватывает это сам
     благодаря "reload_style_on_change": true.

Нужно: python3, grim, waybar >= 0.9.14, niri.
"""
import json
import os
import subprocess
import sys
import threading
import time

BAR_H = 22                      # должно совпадать с "height" в config.jsonc
SAMPLE_Y = BAR_H + 2            # строка пикселей, которую смотрим (чуть ниже панели)
DEBOUNCE = 0.45                 # сек. тишины после события, потом снимок

CFG = os.path.expanduser("~/.config/waybar")
BASE = os.path.join(CFG, "style-base.css")
OUT = os.path.join(CFG, "style.css")

DARK_FG = (24, 24, 37)          # текст на светлой панели
LIGHT_FG = (205, 214, 244)      # текст на тёмной панели

# события, которые не меняют картинку под панелью
IGNORE = ("KeyboardLayout", "ConfigLoaded", "OverviewOpenedOrClosed", "WindowUrgency")


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def niri_json(*args):
    return json.loads(run("niri", "msg", "--json", *args))


def parse_ppm(b):
    """Возвращает (ширина, высота, байты пикселей RGB) из бинарного P6."""
    parts, i = [], 0
    while len(parts) < 4:
        while b[i:i + 1].isspace():
            i += 1
        j = i
        while not b[j:j + 1].isspace():
            j += 1
        parts.append(b[i:j])
        i = j
    return int(parts[1]), int(parts[2]), b[i + 1:]


def median_rgb(data):
    out = []
    for c in range(3):
        ch = sorted(data[c::3])
        out.append(ch[len(ch) // 2])
    return tuple(out)


def sample_color():
    out = niri_json("focused-output")
    width = out["logical"]["width"]
    ppm = run("grim", "-g", f"0,{SAMPLE_Y} {width}x1", "-t", "ppm", "-")
    _, _, data = parse_ppm(ppm)
    return median_rgb(data)


def workspace_has_window():
    for ws in niri_json("workspaces"):
        if ws.get("is_focused"):
            return ws.get("active_window_id") is not None
    return True


def luminance(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def build_css(rgb, has_window):
    fg = DARK_FG if luminance(rgb) > 150 else LIGHT_FG
    bg = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})" if has_window else "rgba(0,0,0,0)"
    dim = f"rgba({fg[0]},{fg[1]},{fg[2]},0.6)"
    return (
        f"@define-color bar_bg {bg};\n"
        f"@define-color bar_fg rgb({fg[0]},{fg[1]},{fg[2]});\n"
        f"@define-color bar_dim {dim};\n\n"
    )


_last = None


def apply():
    global _last
    try:
        css = build_css(sample_color(), workspace_has_window())
    except Exception as e:  # grim/niri недоступны - просто оставляем как есть
        print("adaptive:", e, file=sys.stderr)
        return
    if css == _last:
        return
    with open(BASE, encoding="utf-8") as f:
        base = f.read()
    with open(OUT, "w", encoding="utf-8") as f:   # пишем на месте, чтобы waybar видел изменение
        f.write(css + base)
    _last = css


def main():
    ev = threading.Event()

    def worker():
        while True:
            ev.wait()
            while True:                 # ждём, пока события затихнут
                ev.clear()
                time.sleep(DEBOUNCE)
                if not ev.is_set():
                    break
            apply()

    threading.Thread(target=worker, daemon=True).start()
    ev.set()                            # первый запуск

    p = subprocess.Popen(["niri", "msg", "--json", "event-stream"],
                         stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        if not any(k in line for k in IGNORE):
            ev.set()


if __name__ == "__main__":
    main()
