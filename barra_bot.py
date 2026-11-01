import atexit
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from collections import deque

# Faz as coordenadas baterem com os pixels reais (resolve escala 125%/150% do Windows)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
# Relógio de 1 ms (o padrão do Windows é ~15 ms e deixaria o controle "lento")
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

import keyboard
import mss
import numpy as np
import pyautogui

try:
    import mss.tools as mss_tools
except Exception:
    mss_tools = None

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False

BASE = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
CONFIG = os.path.join(BASE, "config_barra_bot.json")

DEFAULTS = {
    "x": 0,
    "y": 0,
    "altura": 0,
    "largura": 70,
    "antecipacao": 300,   # ms
    "zona_morta": 0.6,    # % da altura da barra
    "alvo_pct": 50,       # 0 = topo do retângulo, 100 = base
    "botao": "left",
    "modo_teste": False,
    "sempre_no_topo": True,
}

# ------------------------------------------------------------------ cores
BG = "#0e1014"
CARD = "#161920"
LINE = "#252a36"
TXT = "#e8eaf0"
MUTED = "#8a92a6"
ACC = "#4f8cff"
OK = "#22c55e"
BAD = "#ef4444"
WARN = "#f59e0b"
OFF = "#2a2f3c"
FONT = ("Segoe UI", 10)
FONT_S = ("Segoe UI", 9)
FONT_B = ("Segoe UI", 10, "bold")

RW, RH = 84, 190      # tamanho do "radar" (visão ao vivo)


# ================================================================ detecção
def maior_faixa(linhas, gap, min_alt):
    """Maior trecho contínuo de linhas True (junta trechos separados por até `gap` linhas).
    Retorna (topo, base) ou None."""
    idx = np.flatnonzero(linhas)
    if idx.size == 0:
        return None
    cortes = np.flatnonzero(np.diff(idx) > gap + 1)
    inicios = np.concatenate(([0], cortes + 1))
    fins = np.concatenate((cortes, [idx.size - 1]))
    melhor, melhor_alt = None, 0
    for a, b in zip(inicios, fins):
        topo, base = int(idx[a]), int(idx[b])
        alt = base - topo + 1
        if alt >= min_alt and alt > melhor_alt:
            melhor, melhor_alt = (topo, base), alt
    return melhor


def grupos(linhas, gap):
    """Lista de trechos (topo, base) de linhas True, juntando os separados por até `gap` linhas."""
    idx = np.flatnonzero(linhas)
    if idx.size == 0:
        return []
    cortes = np.flatnonzero(np.diff(idx) > gap + 1)
    inicios = np.concatenate(([0], cortes + 1))
    fins = np.concatenate((cortes, [idx.size - 1]))
    return [(int(idx[a]), int(idx[b])) for a, b in zip(inicios, fins)]


def achar_retangulo(vivo, mx, h, w):
    """O retângulo tem uma borda colorida: a cor vai de amarelo a verde e o brilho muda conforme o quadrado
    se aproxima (e conforme o fundo, de dia ou de noite). O que NÃO muda é a estrutura: duas linhas
    horizontais largas (topo e base) ou as duas laterais, com ~10% da altura da barra, e ao redor dele
    a barra é escura e vazia. Candidatos que parecem cenário (mancha cheia, escura, fora do centro) caem fora."""
    cont = vivo.sum(axis=1)
    largas = cont >= max(10, int(0.35 * w))
    esperado, lo, hi = 0.105 * h, 0.07 * h, 0.16 * h
    cands = []

    def candidato(topo, base, extra=0.0):
        alt = base - topo + 1
        if lo <= alt <= hi:
            cands.append((abs(alt - esperado) + extra, topo, base))

    linhas = grupos(largas, 2)
    for i in range(len(linhas)):
        candidato(linhas[i][0], linhas[i][1])               # preenchimento também colorido: bloco único
        for j in range(i + 1, len(linhas)):
            candidato(linhas[i][0], linhas[j][1])           # borda de cima + borda de baixo
    for a, b in grupos(cont >= 1, max(2, int(0.012 * h))):  # só as laterais (borda de cima/baixo escura)
        if largas[a:b + 1].any() or (cont[a:b + 1] >= 2).mean() >= 0.70:
            candidato(a, b, extra=1.0)

    def valido(topo, base):
        bloco = vivo[topo:base + 1]
        if bloco.mean() > 0.70:                              # mancha cheia = cenário, não contorno
            return False
        xs = np.flatnonzero(bloco.sum(axis=0) >= 2)
        if xs.size == 0 or (xs.max() - xs.min() + 1) < 0.75 * w:      # precisa cobrir a largura da barra
            return False
        if abs((xs.max() + xs.min()) / 2.0 - (w - 1) / 2.0) > 0.12 * w:  # e estar centrado nela
            return False
        if np.percentile(mx[topo:base + 1][bloco], 90) < 110:          # borda é viva, não verde-escuro
            return False
        return True

    for _, topo, base in sorted(cands):
        if not valido(topo, base):
            continue
        for _ in range(6):                                   # estende até onde ainda há cor
            if topo > 0 and cont[topo - 1] >= 1:
                topo -= 1
            else:
                break
        for _ in range(6):
            if base < h - 1 and cont[base + 1] >= 1:
                base += 1
            else:
                break
        return topo, base
    return None


def detectar(img_bgr):
    """Acha o retângulo e o quadrado numa faixa vertical que cobre a barra inteira.

    - Retângulo: pela BORDA colorida (amarelo a verde, clara ou escura), pela estrutura (ver achar_retangulo).
    - Quadrado: pelo que há de mais CLARO dentro da barra (branco, ou verde-claro quando está no retângulo).
    Retorna (retangulo, quadrado); cada um é (topo, base) em linhas da imagem, ou None.
    """
    h, w = img_bgr.shape[:2]
    f = img_bgr[..., 2::-1].astype(np.int16)          # RGB
    mx = f.max(axis=2)
    mn = f.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    # borda: amarelo -> verde (matiz ~50° a 135°), bem saturada, quase sem azul; descarta madeira/laranja
    vivo = (sat >= 0.80) & (mx >= 50) & (f[..., 2] <= 0.35 * mx) & (f[..., 1] >= 0.75 * f[..., 0])
    claro = (mn >= 160) & (mx >= 205) & ~vivo

    ym_c, ym_b = max(2, int(0.035 * h)), max(2, int(0.02 * h))   # tira a moldura branca de cima/baixo da barra
    x0, x1 = int(w * 0.15), int(w * 0.85)                        # só o miolo (tira as molduras laterais)
    claro[:, :x0] = False
    claro[:, x1:] = False
    claro[:ym_c] = False
    claro[h - ym_b:] = False

    ret = achar_retangulo(vivo, mx, h, w)
    min_px = max(3, int(0.15 * (x1 - x0)))
    sq = maior_faixa(claro.sum(axis=1) >= min_px, gap=2, min_alt=max(4, int(0.03 * h)))
    return ret, sq


# ================================================================ controle
class Controlador:
    """Decide SEGURAR ou SOLTAR o botão.

    O quadrado sobe (acelera) enquanto o botão está pressionado e cai quando solto.
    Segura quando o quadrado PREVISTO (posição + velocidade relativa x antecipação)
    está abaixo do alvo dentro do retângulo.
    """

    def __init__(self):
        self.hist = deque()
        self.hold = False

    def reset(self):
        self.hist.clear()
        self.hold = False

    def _velocidades(self):
        if len(self.hist) < 3:
            return 0.0, 0.0
        t = np.array([h[0] for h in self.hist])
        dt = t[-1] - t[0]
        if dt < 0.02:
            return 0.0, 0.0
        t = t - t.mean()
        den = float((t * t).sum())
        vs = float((t * np.array([h[1] for h in self.hist])).sum() / den)
        vr = float((t * np.array([h[2] for h in self.hist])).sum() / den)
        return vs, vr

    def atualizar(self, t, sq_c, ret_a, ret_b, antecip_s, zona_px, alvo_pct, janela=0.07):
        ret_c = (ret_a + ret_b) / 2.0
        self.hist.append((t, sq_c, ret_c))
        while self.hist and t - self.hist[0][0] > janela:
            self.hist.popleft()
        vs, vr = self._velocidades()
        alvo = ret_a + (ret_b - ret_a) * alvo_pct / 100.0
        e = (sq_c - alvo) + (vs - vr) * antecip_s      # e > 0: quadrado (previsto) abaixo do alvo
        self.hold = e > zona_px if not self.hold else e > -zona_px
        return self.hold, e, vs, vr


# ================================================================ mouse
_BTN_DOWN = {"left": 0x0002, "right": 0x0008}
_BTN_UP = {"left": 0x0004, "right": 0x0010}


def mouse_evento(botao, pressionar):
    try:
        ctypes.windll.user32.mouse_event((_BTN_DOWN if pressionar else _BTN_UP)[botao], 0, 0, 0, 0)
    except Exception:
        if pressionar:
            pyautogui.mouseDown(button=botao)
        else:
            pyautogui.mouseUp(button=botao)


class App:
    def __init__(self):
        self.cfg = DEFAULTS.copy()
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("Barra Bot")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.posicionada = (self.cfg["x"] != 0 or self.cfg["y"] != 0) and self.cfg["altura"] >= 40
        self.ligado = False                           # sempre começa desligado: você liga com F6
        self.contando = False
        self.ajustes_abertos = False
        self.acoes = deque()
        self.ov_estado = None
        self.ov_visivel = False

        # estado compartilhado com a thread de controle
        self.p = {}
        self.snap = {}
        self.ctl = Controlador()
        self.pressionado = None            # botão que está pressionado agora (ou None)
        self.t_press = 0.0                 # quando o botão foi pressionado
        self.rodando = True
        self.pedir_captura = False         # pedido de "foto" do que o bot enxerga (diagnóstico)
        self.msg_captura = ""
        self.msg_captura_ate = 0.0

        # variáveis da interface
        self.v_x = tk.IntVar(value=self.cfg["x"])
        self.v_y = tk.IntVar(value=self.cfg["y"])
        self.v_h = tk.IntVar(value=self.cfg["altura"])
        self.v_w = tk.IntVar(value=self.cfg["largura"])
        self.v_ant = tk.IntVar(value=self.cfg["antecipacao"])
        self.v_zona = tk.DoubleVar(value=self.cfg["zona_morta"])
        self.v_alvo = tk.IntVar(value=self.cfg["alvo_pct"])
        self.v_btn = tk.StringVar(value=self.cfg["botao"])
        self.v_teste = tk.BooleanVar(value=self.cfg["modo_teste"])
        self.v_top = tk.BooleanVar(value=self.cfg["sempre_no_topo"])

        self.montar_ui()
        self.criar_overlay()
        self.registrar_atalhos()
        self.sincronizar()

        self.root.attributes("-topmost", self.v_top.get())
        self.escurecer_barra_titulo()
        self.root.protocol("WM_DELETE_WINDOW", self.fechar)
        atexit.register(self.soltar)
        if not self.posicionada:
            self.alternar_ajustes()                    # primeira vez: já abre a configuração

        self.thread = threading.Thread(target=self.laco_controle, daemon=True)
        self.thread.start()
        self.gui_tick()

    # ------------------------------------------------------- helpers de UI
    def botao(self, parent, texto, cmd, cor=LINE, fg=TXT):
        return tk.Button(
            parent, text=texto, command=cmd, bg=cor, fg=fg,
            activebackground=ACC, activeforeground="white",
            relief="flat", bd=0, padx=10, pady=7, font=FONT, cursor="hand2",
        )

    def check(self, parent, texto, var, cmd=None):
        return tk.Checkbutton(
            parent, text=texto, variable=var, command=cmd, bg=CARD, fg=TXT,
            selectcolor=BG, activebackground=CARD, activeforeground=TXT,
            bd=0, highlightthickness=0, font=FONT_S, anchor="w",
        )

    def escurecer_barra_titulo(self):
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
        except Exception:
            pass

    # ------------------------------------------------------------------ UI
    def montar_ui(self):
        r = self.root
        pad = 16

        # cabeçalho
        cab = tk.Frame(r, bg=BG)
        cab.pack(fill="x", padx=pad, pady=(16, 12))
        tk.Label(cab, text="Barra Bot", bg=BG, fg=TXT, font=("Segoe UI", 15, "bold")).pack(side="left")
        self.pill = tk.Label(
            cab, text="●  DESLIGADO", bg=OFF, fg=MUTED, font=("Segoe UI", 9, "bold"), padx=10, pady=3
        )
        self.pill.pack(side="right")

        # radar + estado
        meio = tk.Frame(r, bg=BG)
        meio.pack(fill="x", padx=pad)
        self.cv = tk.Canvas(
            meio, width=RW, height=RH, bg=CARD, highlightthickness=1, highlightbackground=LINE
        )
        self.cv.pack(side="left")
        cx = RW / 2
        self.rd_bar = self.cv.create_rectangle(cx - 14, 6, cx + 14, RH - 6, fill="#12151b", outline=LINE)
        self.rd_ret = self.cv.create_rectangle(0, 0, 1, 1, fill="#365314", outline="#4ade80", width=2, state="hidden")
        self.rd_sq = self.cv.create_rectangle(0, 0, 1, 1, fill="#f5f5f5", outline="", state="hidden")

        txt = tk.Frame(meio, bg=BG)
        txt.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.lbl_estado = tk.Label(
            txt, text="", bg=BG, fg=TXT, font=("Segoe UI", 11, "bold"),
            wraplength=190, justify="left", anchor="w",
        )
        self.lbl_estado.pack(anchor="w", pady=(8, 4))
        self.lbl_detalhe = tk.Label(
            txt, text="", bg=BG, fg=MUTED, font=FONT_S, wraplength=190, justify="left", anchor="w",
        )
        self.lbl_detalhe.pack(anchor="w")
        self.lbl_seq = tk.Label(txt, text="", bg=BG, fg=MUTED, font=FONT_S, anchor="w")
        self.lbl_seq.pack(anchor="w", pady=(10, 0))

        # botões principais
        self.btn_toggle = self.botao(r, "Ligar  (F6)", self.alternar, cor=ACC, fg="white")
        self.btn_toggle.config(font=FONT_B, pady=9)
        self.btn_toggle.pack(fill="x", padx=pad, pady=(14, 6))
        self.btn_ajustes = tk.Button(
            r, text="⚙  Ajustes", command=self.alternar_ajustes, bg=BG, fg=MUTED,
            activebackground=BG, activeforeground=TXT, relief="flat", bd=0,
            font=FONT_S, cursor="hand2", pady=4,
        )
        self.btn_ajustes.pack(pady=(0, 6))

        # painel de ajustes (escondido por padrão)
        self.painel = tk.Frame(r, bg=LINE)
        dentro = tk.Frame(self.painel, bg=CARD)
        dentro.pack(fill="both", expand=True, padx=1, pady=1)
        corpo = tk.Frame(dentro, bg=CARD)
        corpo.pack(fill="x", padx=14, pady=12)
        corpo.grid_columnconfigure(0, weight=1)
        corpo.grid_columnconfigure(1, weight=1)

        self.lbl_regiao = tk.Label(corpo, text="", bg=CARD, fg=MUTED, font=FONT_S, anchor="w")
        self.lbl_regiao.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.btn_topo = self.botao(corpo, "Marcar topo  (F7)", lambda: self.contar("topo"))
        self.btn_topo.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.btn_base = self.botao(corpo, "Marcar base  (F8)", lambda: self.contar("base"))
        self.btn_base.grid(row=1, column=1, sticky="ew", padx=(4, 0))
        tk.Label(
            corpo, text="Aponte o mouse para o meio da barra: topo (F7), depois base (F8).",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=250,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 10))

        tk.Label(corpo, text="Antecipação (ms)", bg=CARD, fg=MUTED, font=FONT_S).grid(row=3, column=0, sticky="w")
        tk.Spinbox(
            corpo, from_=0, to=600, increment=10, textvariable=self.v_ant, width=6,
            bg=BG, fg=TXT, insertbackground=TXT, buttonbackground=LINE, relief="flat",
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACC, font=FONT,
        ).grid(row=3, column=1, sticky="e", pady=3)

        tk.Label(corpo, text="Botão do mouse", bg=CARD, fg=MUTED, font=FONT_S).grid(row=4, column=0, sticky="w", pady=(4, 0))
        rb = tk.Frame(corpo, bg=CARD)
        rb.grid(row=4, column=1, sticky="e", pady=(4, 0))
        for txt_, val in (("Esquerdo", "left"), ("Direito", "right")):
            tk.Radiobutton(
                rb, text=txt_, value=val, variable=self.v_btn, bg=CARD, fg=TXT,
                selectcolor=BG, activebackground=CARD, activeforeground=TXT,
                bd=0, highlightthickness=0, font=FONT_S,
            ).pack(side="left", padx=(8, 0))

        self.check(corpo, "Modo teste (só observa, não clica)", self.v_teste).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.check(
            corpo, "Manter esta janela sempre no topo", self.v_top,
            cmd=lambda: self.root.attributes("-topmost", self.v_top.get()),
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        self.botao(corpo, "Salvar foto do que o bot vê", self.salvar_captura).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        tk.Label(
            r, text="F6 liga/desliga  •  ligue com a barra na tela e o jogo em foco", bg=BG, fg=MUTED, font=("Segoe UI", 8),
        ).pack(side="bottom", pady=(0, 10))

    def salvar_captura(self):
        """Pede à thread de controle para salvar a imagem exata da região lida (diagnostico.png)."""
        self.pedir_captura = True
        self.msg_captura = "Salvando…"
        self.msg_captura_ate = time.time() + 5

    def alternar_ajustes(self):
        self.ajustes_abertos = not self.ajustes_abertos
        if self.ajustes_abertos:
            self.painel.pack(fill="x", padx=16, pady=(0, 10), after=self.btn_ajustes)
            self.btn_ajustes.config(text="▲  Fechar ajustes")
        else:
            self.painel.pack_forget()
            self.btn_ajustes.config(text="⚙  Ajustes")
            self.ov_estado = None

    # ------------------------------------------------------------- overlay
    def criar_overlay(self):
        """Contorno da região (só aparece com os Ajustes abertos), por fora da área lida."""
        self.ov = tk.Toplevel(self.root)
        self.ov.overrideredirect(True)
        self.ov.attributes("-topmost", True)
        self.ov.configure(bg="#010101")
        self.ov.attributes("-transparentcolor", "#010101")
        self.ov_cv = tk.Canvas(self.ov, bg="#010101", highlightthickness=0, bd=0)
        self.ov_cv.pack(fill="both", expand=True)
        self.ov_ret = self.ov_cv.create_rectangle(1, 1, 2, 2, outline="#4f8cff", width=2)
        self.ov.geometry("1x1+0+0")
        self.ov.update_idletasks()
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.ov.winfo_id()) or self.ov.winfo_id()
            estilo = user32.GetWindowLongW(hwnd, -20)
            # layered + click-through + toolwindow + NOACTIVATE (nunca rouba o foco do jogo)
            user32.SetWindowLongW(hwnd, -20, estilo | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass
        self.ov.withdraw()

    # ------------------------------------------------------------- atalhos
    def registrar_atalhos(self):
        # os atalhos rodam em outra thread, então só enfileiramos a ação
        keyboard.add_hotkey("F6", lambda: self.acoes.append("toggle"))
        keyboard.add_hotkey("F7", lambda: self.acoes.append("topo"))
        keyboard.add_hotkey("F8", lambda: self.acoes.append("base"))

    def processar_acoes(self):
        while self.acoes:
            a = self.acoes.popleft()
            if a == "toggle":
                self.alternar()
            else:
                self.executar(a)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def ler(var, padrao):
        try:
            return var.get()
        except Exception:
            return padrao

    def contar(self, qual, n=3):
        if self.contando:
            return
        self.contando = True
        self._contagem(qual, n)

    def _contagem(self, qual, n):
        btn, texto = {
            "topo": (self.btn_topo, "Marcar topo  (F7)"),
            "base": (self.btn_base, "Marcar base  (F8)"),
        }[qual]
        if n > 0:
            btn.config(text=f"Leve o mouse…  {n}")
            self.root.after(1000, lambda: self._contagem(qual, n - 1))
        else:
            btn.config(text=texto)
            self.contando = False
            self.executar(qual)

    def executar(self, qual):
        p = pyautogui.position()
        if qual == "topo":
            self.v_x.set(p.x)
            self.v_y.set(p.y)
            self.v_h.set(0)
            self.posicionada = False
            self.ligado = False
            self.soltar()
            if not self.ajustes_abertos:
                self.alternar_ajustes()
        elif qual == "base":
            altura = p.y - self.ler(self.v_y, 0)
            if altura < 40:
                return                                   # base precisa ficar abaixo do topo
            self.v_h.set(altura)
            self.posicionada = True
            self.ctl.reset()

    def alternar(self):
        if not self.posicionada:
            if not self.ajustes_abertos:
                self.alternar_ajustes()
            return
        self.ligado = not self.ligado
        self.p["ligado"] = self.ligado          # a thread já enxerga na hora (sem esperar o painel)
        if not self.ligado:
            self.soltar()
            self.ctl.reset()

    def sincronizar(self):
        """Copia os valores da interface para a thread de controle."""
        h = max(0, self.ler(self.v_h, 0))
        self.p = {
            "ligado": self.ligado,
            "posicionada": self.posicionada,
            "x": self.ler(self.v_x, 0),
            "y": self.ler(self.v_y, 0),
            "h": h,
            "w": max(30, self.ler(self.v_w, 70)),
            "antecip": max(0, self.ler(self.v_ant, 300)) / 1000.0,
            "zona_px": max(0.0, self.ler(self.v_zona, 0.6)) / 100.0 * max(h, 1),
            "alvo": min(100, max(0, self.ler(self.v_alvo, 50))),
            "botao": self.v_btn.get(),
            "teste": self.v_teste.get(),
        }

    # -------------------------------------------------------------- mouse
    def segurar(self, quer):
        """Mantém o botão do mouse pressionado (quer=True) ou solto (quer=False)."""
        if quer and self.pressionado is None:
            btn = self.p.get("botao", "left")
            mouse_evento(btn, True)
            self.pressionado = btn
            self.t_press = time.perf_counter()
        elif not quer and self.pressionado is not None:
            mouse_evento(self.pressionado, False)
            self.pressionado = None

    def soltar(self):
        self.segurar(False)

    # ------------------------------------------------- thread de controle
    def laco_controle(self):
        sct = mss.mss()           # o capturador precisa ser criado dentro da própria thread
        dentro_desde = None
        melhor = 0.0
        ultimo_ok = 0.0
        seguidos = 0
        hold = False
        controlando = False
        bloqueio_ate = 0.0
        travou = 0
        while self.rodando:
            try:
                p = self.p
                if not p.get("posicionada") or p.get("h", 0) < 40:
                    self.soltar()
                    self.snap = {"achou": False, "controlando": False, "ret": None, "sq": None, "h": 1, "hold": False}
                    time.sleep(0.03)
                    continue
                w, h = p["w"], p["h"]
                left = p["x"] - w // 2
                try:
                    shot = sct.grab({"left": left, "top": p["y"], "width": w, "height": h})
                    img = np.array(shot)
                except Exception:
                    sct = mss.mss()
                    time.sleep(0.01)
                    continue
                t = time.perf_counter()
                if self.pedir_captura:
                    self.pedir_captura = False
                    try:
                        caminho = os.path.join(BASE, "diagnostico.png")
                        mss_tools.to_png(shot.rgb, shot.size, output=caminho)
                        self.msg_captura = "Foto salva: diagnostico.png (na pasta do app)"
                    except Exception as ex:
                        self.msg_captura = f"Não consegui salvar: {ex}"
                    self.msg_captura_ate = time.time() + 6
                ret, sq = detectar(img)
                achou = ret is not None and sq is not None

                if achou:
                    ultimo_ok = t
                    seguidos += 1
                else:
                    seguidos = 0

                ativo = p["ligado"] and not p["teste"]
                controlando = False
                if ativo and achou and seguidos >= 3:
                    sq_c = (sq[0] + sq[1]) / 2.0
                    hold, _, vs, _ = self.ctl.atualizar(
                        t, sq_c, ret[0], ret[1], p["antecip"], p["zona_px"], p["alvo"]
                    )
                    # ---- travas: o botão NUNCA fica preso ----
                    if hold and t < bloqueio_ate:
                        hold = False                                    # pausa forçada em andamento
                    elif hold and self.pressionado is not None:
                        preso = t - self.t_press
                        if preso > 1.4 or (preso > 0.5 and abs(vs) < 12):
                            # segurou tempo demais, ou o quadrado não se mexe com o botão apertado
                            hold = False
                            self.ctl.hold = False
                            bloqueio_ate = t + 0.15
                            travou += 1
                        elif preso > 0.2 and abs(vs) > 40:
                            travou = 0                                  # o quadrado responde ao botão
                    self.segurar(hold)
                    controlando = True
                elif not achou and t - ultimo_ok > 0.25:
                    # barra sumiu (minigame acabou): solta e esquece o histórico
                    hold = False
                    travou = 0
                    self.soltar()
                    self.ctl.reset()
                elif not ativo:
                    hold = False
                    self.soltar()

                # tempo dentro do retângulo (centro do quadrado dentro do retângulo)
                if achou and ret[0] <= (sq[0] + sq[1]) / 2.0 <= ret[1]:
                    if dentro_desde is None:
                        dentro_desde = t
                elif dentro_desde is not None:
                    melhor = max(melhor, t - dentro_desde)
                    dentro_desde = None
                if not achou and t - ultimo_ok > 1.0:
                    melhor = 0.0                          # nova rodada: zera o recorde

                self.snap = {
                    "ret": ret, "sq": sq, "h": h, "achou": achou, "controlando": controlando, "travou": travou,
                    "hold": self.pressionado is not None,
                    "seq": (t - dentro_desde) if dentro_desde is not None else 0.0,
                    "melhor": max(melhor, (t - dentro_desde) if dentro_desde is not None else 0.0),
                }
            except Exception as ex:
                self.snap = {"erro": repr(ex), "achou": False, "controlando": False, "ret": None, "sq": None, "h": 1, "hold": False}
                self.soltar()
                time.sleep(0.05)
            time.sleep(0.001)
        self.soltar()

    # -------------------------------------------------------- painel (GUI)
    def desenhar_radar(self, s):
        h = max(s.get("h", 1), 1)

        def yr(v):
            return 6 + (v / h) * (RH - 12)

        cx = RW / 2
        ret, sq = s.get("ret"), s.get("sq")
        if ret is not None:
            self.cv.coords(self.rd_ret, cx - 22, yr(ret[0]), cx + 22, yr(ret[1] + 1))
            self.cv.itemconfig(self.rd_ret, state="normal")
        else:
            self.cv.itemconfig(self.rd_ret, state="hidden")
        if sq is not None:
            self.cv.coords(self.rd_sq, cx - 9, yr(sq[0]), cx + 9, yr(sq[1] + 1))
            self.cv.itemconfig(self.rd_sq, state="normal", fill="#fbbf24" if s.get("hold") else "#f5f5f5")
        else:
            self.cv.itemconfig(self.rd_sq, state="hidden")

    def definir_estado(self, pill, cor_pill, fg_pill, titulo, detalhe, cor_titulo=TXT):
        self.pill.config(text=f"●  {pill}", bg=cor_pill, fg=fg_pill)
        self.lbl_estado.config(text=titulo, fg=cor_titulo)
        self.lbl_detalhe.config(text=detalhe)

    def gui_tick(self):
        try:
            self.processar_acoes()
            self.sincronizar()
            self.atualizar_contorno()
            s = self.snap
            teste = self.v_teste.get()

            # botão principal
            if not self.posicionada:
                self.btn_toggle.config(text="Marcar a barra primeiro", bg=LINE, fg=MUTED, activebackground=LINE)
            elif self.ligado:
                self.btn_toggle.config(text="Desligar  (F6)", bg=BAD, fg="white", activebackground=BAD)
            else:
                self.btn_toggle.config(text="Ligar  (F6)", bg=ACC, fg="white", activebackground=ACC)

            # texto de região (dentro dos ajustes)
            if time.time() < self.msg_captura_ate:
                self.lbl_regiao.config(text=self.msg_captura, fg=ACC)
            else:
                self.lbl_regiao.config(
                    text=f"Barra marcada ✔  ({self.ler(self.v_h, 0)} px de altura)" if self.posicionada
                    else "Barra ainda não marcada",
                    fg=OK if self.posicionada else WARN,
                )

            if "erro" in s:
                self.definir_estado("ERRO", BAD, "white", "Algo deu errado", s["erro"], BAD)
            elif not self.posicionada:
                self.definir_estado("CONFIGURAR", WARN, "#1a1a1a", "Marque a barra",
                                    "Abra os ajustes: aponte o mouse para o topo (F7) e para a base (F8).", WARN)
            elif not self.ligado:
                self.definir_estado("DESLIGADO", OFF, MUTED, "Desligado", "Aperte F6 para ligar.", MUTED)
            elif teste:
                if s.get("achou"):
                    self.definir_estado("TESTE", ACC, "white", "Vendo a barra", "Modo teste: não está clicando.", OK)
                else:
                    self.definir_estado("TESTE", ACC, "white", "Aguardando a barra", "Modo teste: não está clicando.", MUTED)
            elif s.get("controlando"):
                if s.get("travou", 0) >= 2:
                    self.definir_estado("LIGADO", OK, "white", "Quadrado não responde",
                                        "Clique na janela do jogo para dar foco e tente de novo.", WARN)
                else:
                    self.definir_estado("LIGADO", OK, "white", "Controlando",
                                        "Segurando ▲" if s.get("hold") else "Solto ▼", OK)
            elif s.get("achou"):
                self.definir_estado("LIGADO", OK, "white", "Barra detectada", "Começando…", WARN)
            else:
                ret, sq = s.get("ret"), s.get("sq")
                if (ret is None) != (sq is None):
                    det = "Vejo só uma parte da barra. Confira a região nos ajustes."
                else:
                    det = "Comece o minigame: ele controla assim que a barra aparecer."
                self.definir_estado("LIGADO", OK, "white", "Aguardando a barra", det, TXT)

            if s.get("achou"):
                self.lbl_seq.config(text=f"Dentro há {s.get('seq', 0):.1f} s   •   melhor {s.get('melhor', 0):.1f} s")
            else:
                self.lbl_seq.config(text="")
            if s and self.posicionada:
                self.desenhar_radar(s)
        except Exception as ex:
            print("Erro no painel:", repr(ex))
        finally:
            self.root.after(40, self.gui_tick)

    def atualizar_contorno(self):
        if self.ajustes_abertos and self.posicionada:
            x, y = self.ler(self.v_x, 0), self.ler(self.v_y, 0)
            w, h = max(30, self.ler(self.v_w, 70)), self.ler(self.v_h, 0)
            est = (x, y, w, h)
            if est != self.ov_estado and h >= 40:
                left = x - w // 2
                self.ov.geometry(f"{w + 8}x{h + 8}+{left - 4}+{y - 4}")
                self.ov_cv.coords(self.ov_ret, 1, 1, w + 7, h + 7)
                if not self.ov_visivel:
                    self.ov.deiconify()
                    self.ov_visivel = True
                self.ov_estado = est
        elif self.ov_visivel:
            self.ov.withdraw()
            self.ov_visivel = False
            self.ov_estado = None

    # --------------------------------------------------------------- saída
    def fechar(self):
        self.rodando = False
        self.ligado = False
        self.soltar()
        try:
            self.cfg.update({
                "x": self.ler(self.v_x, 0),
                "y": self.ler(self.v_y, 0),
                "altura": self.ler(self.v_h, 0),
                "largura": self.ler(self.v_w, 70),
                "antecipacao": self.ler(self.v_ant, 300),
                "zona_morta": self.ler(self.v_zona, 0.6),
                "alvo_pct": self.ler(self.v_alvo, 50),
                "botao": self.v_btn.get(),
                "modo_teste": self.v_teste.get(),
                "sempre_no_topo": self.v_top.get(),
            })
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass
        keyboard.unhook_all()
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        time.sleep(0.05)
        self.root.destroy()

    def rodar(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().rodar()
