import tkinter as tk
from PIL import Image, ImageTk, ImageDraw, ImageFilter
import subprocess
import sys
import os
import math
import random

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(BASE_DIR, "xianyu_scraper.py")
LOGO_PATH = os.path.join(BASE_DIR, "neon_fish_logo.png")
AGENT_SCRIPT = os.path.join(BASE_DIR, "app_standalone.py")
REVIEW_SCRIPT = os.path.join(BASE_DIR, "xianyu_review.py")

W, H = 520, 620
BG = "#0d0d1a"
CYAN = "#00e5ff"
PURPLE = "#a855f7"
MAGENTA = "#ec4899"
DIM_CYAN = "#0a3d4a"
DIM_PURPLE = "#2d1854"
DIM_MAGENTA = "#4a1230"
CARD_BG = "#13132a"
CARD_BORDER = "#1e1e3f"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def lerp_color(c1, c2, t):
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def run_scraper():
    subprocess.Popen([sys.executable, "-X", "utf8", SCRIPT_PATH], cwd=BASE_DIR)


def run_agent():
    # 保留可见控制台：静默失败正是之前"点了没反应"的根源
    # 显式空标题：路径含空格，否则 start 会把它当窗口标题而不启动任何东西
    subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k",
                      sys.executable, "-X", "utf8", AGENT_SCRIPT], cwd=BASE_DIR)


def run_review():
    subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k",
                      sys.executable, "-X", "utf8", REVIEW_SCRIPT], cwd=BASE_DIR)


root = tk.Tk()
root.title("霓虹鱼 NeonFish")
root.configure(bg=BG)
root.resizable(False, False)
root.overrideredirect(False)

try:
    icon = Image.open(LOGO_PATH)
    root.iconphoto(True, ImageTk.PhotoImage(icon))
except Exception:
    pass

canvas = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
canvas.pack()

particles = []
for _ in range(40):
    particles.append({
        "x": random.randint(0, W),
        "y": random.randint(0, H),
        "r": random.uniform(0.5, 2.0),
        "speed": random.uniform(0.1, 0.4),
        "alpha": random.randint(40, 120),
        "color": random.choice([CYAN, PURPLE, MAGENTA]),
    })


def draw_bg():
    for i in range(60):
        t = i / 60
        color = lerp_color("#0d0d1a", "#0f1629", t)
        canvas.create_line(0, i * (H // 60), W, i * (H // 60), fill=color, width=H // 60 + 1)
    canvas.create_oval(W // 2 - 200, -100, W // 2 + 200, 200, fill="#0f1a2e", outline="")
    canvas.create_oval(-80, H - 200, 160, H + 40, fill="#110f24", outline="")
    canvas.create_oval(W - 100, H - 160, W + 80, H + 20, fill="#0f1424", outline="")


def draw_particles():
    for p in particles:
        brightness = p["alpha"] / 255
        color = lerp_color(BG, p["color"], brightness * 0.6)
        canvas.create_oval(
            p["x"] - p["r"], p["y"] - p["r"],
            p["x"] + p["r"], p["y"] + p["r"],
            fill=color, outline=""
        )


def animate_particles():
    for p in particles:
        p["y"] -= p["speed"]
        if p["y"] < -5:
            p["y"] = H + 5
            p["x"] = random.randint(0, W)
    canvas.delete("particle")
    for p in particles:
        brightness = p["alpha"] / 255
        color = lerp_color(BG, p["color"], brightness * 0.6)
        canvas.create_oval(
            p["x"] - p["r"], p["y"] - p["r"],
            p["x"] + p["r"], p["y"] + p["r"],
            fill=color, outline="", tags="particle"
        )
    root.after(50, animate_particles)


def draw_glow_ring(cx, cy, radius):
    for i in range(8, 0, -1):
        r = radius + i * 3
        alpha = 0.04 * (9 - i)
        color = lerp_color(BG, CYAN, alpha)
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2)


def draw_logo():
    try:
        img = Image.open(LOGO_PATH).convert("RGBA")
        size = 110
        img = img.resize((size, size), Image.LANCZOS)

        glow = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        draw.ellipse([20, 20, size + 20, size + 20], fill=(0, 229, 255, 30))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
        glow_img = Image.alpha_composite(glow, Image.new("RGBA", glow.size, (0, 0, 0, 0)))
        glow_img.paste(img, (20, 20), img)

        photo = ImageTk.PhotoImage(glow_img)
        cx, cy = W // 2, 160
        canvas.create_image(cx, cy, image=photo, tags="logo")
        canvas.image_ref = photo
        draw_glow_ring(cx, cy, size // 2 + 5)
    except Exception:
        pass


def draw_title():
    canvas.create_text(W // 2, 245, text="霓虹鱼", font=("Microsoft YaHei", 26, "bold"), fill=CYAN)
    canvas.create_text(W // 2, 278, text="N E O N F I S H", font=("Consolas", 11), fill="#6b7fa8")
    canvas.create_line(W // 2 - 80, 300, W // 2 + 80, 300, fill=CARD_BORDER, width=1)


def draw_rounded_rect(x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class NeonButton:
    def __init__(self, x, y, w, h, text, icon_char, color, dim_color, command):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.text = text
        self.icon_char = icon_char
        self.color = color
        self.dim_color = dim_color
        self.command = command
        self.hovered = False
        self.pressed = False
        self.items = []
        self.draw()

    def draw(self):
        for item in self.items:
            canvas.delete(item)
        self.items = []

        x1, y1 = self.x, self.y
        x2, y2 = self.x + self.w, self.y + self.h
        r = 14

        if self.hovered:
            for i in range(4, 0, -1):
                glow_color = lerp_color(BG, self.color, 0.08 * (5 - i))
                gid = draw_rounded_rect(
                    x1 - i * 2, y1 - i * 2, x2 + i * 2, y2 + i * 2,
                    r + i, fill=glow_color, outline=""
                )
                self.items.append(gid)

        border_color = self.color if self.hovered else self.dim_color
        fill_color = lerp_color(CARD_BG, self.dim_color, 0.4 if self.hovered else 0.15)

        bid = draw_rounded_rect(x1, y1, x2, y2, r, fill=fill_color, outline=border_color, width=2 if self.hovered else 1)
        self.items.append(bid)

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        iid = canvas.create_text(cx, cy - 10, text=self.icon_char, font=("Segoe UI Emoji", 20), fill=self.color if self.hovered else "#8899aa")
        self.items.append(iid)

        tid = canvas.create_text(cx, cy + 18, text=self.text, font=("Microsoft YaHei", 11, "bold"), fill="#e8e8f0" if self.hovered else "#a0a8c0")
        self.items.append(tid)

    def hit_test(self, mx, my):
        return self.x <= mx <= self.x + self.w and self.y <= my <= self.y + self.h

    def on_enter(self):
        if not self.hovered:
            self.hovered = True
            self.draw()
            canvas.configure(cursor="hand2")

    def on_leave(self):
        if self.hovered:
            self.hovered = False
            self.pressed = False
            self.draw()
            canvas.configure(cursor="")

    def on_click(self):
        self.command()


btn_w, btn_h = 150, 90
btn_y = 340
gap = 20
total_w = btn_w * 3 + gap * 2
start_x = (W - total_w) // 2

buttons = [
    NeonButton(start_x, btn_y, btn_w, btn_h, "打开 Agent", "🤖", CYAN, DIM_CYAN, run_agent),
    NeonButton(start_x + btn_w + gap, btn_y, btn_w, btn_h, "运行抓单脚本", "🐟", PURPLE, DIM_PURPLE, run_scraper),
    NeonButton(start_x + (btn_w + gap) * 2, btn_y, btn_w, btn_h, "闲鱼数据填入", "📥", MAGENTA, DIM_MAGENTA, run_review),
]


def draw_status_card():
    x1, y1 = 40, 460
    x2, y2 = W - 40, 570
    draw_rounded_rect(x1, y1, x2, y2, 12, fill=CARD_BG, outline=CARD_BORDER, width=1)

    canvas.create_text(x1 + 20, y1 + 25, text="●", font=("Consolas", 8), fill="#22c55e", anchor="w")
    canvas.create_text(x1 + 38, y1 + 25, text="系统就绪", font=("Microsoft YaHei", 9), fill="#6b7fa8", anchor="w")

    items = [
        ("抓取引擎", "Playwright + Chromium"),
        ("数据存储", "MySQL neon_ledger"),
        ("目标平台", "goofish.com"),
    ]
    for i, (label, value) in enumerate(items):
        row_y = y1 + 50 + i * 28
        canvas.create_text(x1 + 20, row_y, text=label, font=("Microsoft YaHei", 9), fill="#5a6580", anchor="w")
        canvas.create_text(x2 - 20, row_y, text=value, font=("Consolas", 9), fill="#8899bb", anchor="e")


def draw_footer():
    canvas.create_text(W // 2, H - 22, text="v1.0  ·  Built with NeonFish Engine", font=("Consolas", 8), fill="#3a3f5c")


def on_motion(event):
    for btn in buttons:
        if btn.hit_test(event.x, event.y):
            btn.on_enter()
        else:
            btn.on_leave()


def on_click(event):
    for btn in buttons:
        if btn.hit_test(event.x, event.y):
            btn.on_click()
            break


canvas.bind("<Motion>", on_motion)
canvas.bind("<Button-1>", on_click)

draw_bg()
draw_particles()
draw_logo()
draw_title()
for btn in buttons:
    btn.draw()
draw_status_card()
draw_footer()
animate_particles()

root.eval("tk::PlaceWindow . center")
root.mainloop()
