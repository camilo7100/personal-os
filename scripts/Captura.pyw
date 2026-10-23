"""Captura rápida: ventana mínima (atajo Ctrl+Alt+N). Escribe, Enter y listo: va al inbox. Esc cierra."""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pos import db, services  # noqa: E402

BG, FG, ACCENT = "#171a23", "#e8eaf2", "#818cf8"


def save(event=None):
    text = entry.get().strip()
    if not text:
        return
    with db.session() as con:
        n = services.add_inbox(con, text, "captura-rapida", "user")
    entry.delete(0, tk.END)
    status.config(text=f"✓ capturado #{n}", fg="#4ade80")
    root.after(650, root.destroy)


root = tk.Tk()
root.title("Captura rápida · PERSONAL_OS")
root.configure(bg=BG)
root.attributes("-topmost", True)
w, h = 620, 96
root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{root.winfo_screenheight() // 4}")
root.resizable(False, False)
tk.Label(root, text="Captura primero, clasifica después", bg=BG, fg="#9096ab", font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 0))
entry = tk.Entry(root, font=("Segoe UI", 14), bg="#0f1117", fg=FG, insertbackground=FG, relief="flat",
                 highlightthickness=2, highlightbackground="#262a38", highlightcolor=ACCENT)
entry.pack(fill="x", padx=16, pady=6, ipady=6)
status = tk.Label(root, text="Enter = guardar · Esc = cerrar", bg=BG, fg="#9096ab", font=("Segoe UI", 9))
status.pack(anchor="w", padx=16)
entry.bind("<Return>", save)
root.bind("<Escape>", lambda e: root.destroy())
entry.focus_force()
root.mainloop()
