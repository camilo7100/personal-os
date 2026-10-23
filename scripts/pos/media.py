"""Multimedia: video (FFmpeg), imagen (Pillow), audio→texto (faster-whisper local), PDF y OCR (PyMuPDF + visión local).
Carpeta de arrastrar y soltar: cada subcarpeta de Documents\\PERSONAL_OS-Media\\ es una acción; el job `media-watch` procesa lo que caiga."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import typer

from . import db

app = typer.Typer(help="Video, imagen, audio y PDF", no_args_is_help=True)
MEDIA_HOME = Path.home() / "Documents" / "PERSONAL_OS-Media"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac"}


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for p in (db.LOCALAPPDATA / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
        return str(p)
    raise RuntimeError("ffmpeg no encontrado (winget install Gyan.FFmpeg)")


def _run(args: list[str], timeout: int = 4 * 3600) -> None:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + " ".join(r.stderr.strip().splitlines()[-2:])[:300])


def _out(src: Path, suffix: str, tag: str, out_dir: Path | None = None) -> Path:
    d = out_dir or src.parent
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{src.stem}-{tag}{suffix}"


# ---------- editores gráficos (programas de escritorio para trabajo creativo) ----------
_LOCAL = db.LOCALAPPDATA
_PF = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
EDITORS = {
    "video": ("Shotcut — edición de video (multipista, filtros, keyframes)", [_LOCAL / "Programs/Shotcut/shotcut.exe", *[p / "Shotcut/shotcut.exe" for p in _PF]]),
    "imagen": ("GIMP 3 — retoque de fotos y diseño de imágenes", [_LOCAL / "Programs/GIMP 3/bin/gimp-3.exe", *[p / "GIMP 3/bin/gimp-3.exe" for p in _PF]]),
    "vector": ("Inkscape — logos, ilustración vectorial", [_LOCAL / "Programs/Inkscape/bin/inkscape.exe", *[p / "Inkscape/bin/inkscape.exe" for p in _PF]]),
    "mejorar": ("Upscayl — mejora y amplía imágenes con IA (local)", [_LOCAL / "Programs/upscayl/Upscayl.exe", *[p / "Upscayl/Upscayl.exe" for p in _PF]]),
    "resolve": ("DaVinci Resolve — video profesional y color", [p / "Blackmagic Design/DaVinci Resolve/Resolve.exe" for p in _PF]),
}


def editors() -> list[dict]:
    out = []
    for key, (label, paths) in EDITORS.items():
        found = next((p for p in paths if p.exists()), None)
        out.append({"key": key, "label": label, "path": str(found) if found else None})
    return out


def open_editor(key: str, file: str | None = None) -> str:
    """Abre el editor gráfico (opcionalmente con un archivo). Solo se ejecuta desde el PC (el portal lo bloquea en remoto)."""
    ed = next((e for e in editors() if e["key"] == key), None)
    if not ed or not ed["path"]:
        raise RuntimeError(f"{key} no está instalado (ver automation\\Install-Editors-Admin.ps1)")
    subprocess.Popen([ed["path"], *([file] if file else [])], close_fds=True)
    with db.session() as con:
        db.audit(con, "media.editor", "LOCAL_ACTION", key, detail=file or "")
    return ed["label"]


# ---------- video ----------
def video_compress(src: Path, crf: int = 27, out_dir: Path | None = None) -> Path:
    """H.264 + AAC; CRF 27 ≈ buen equilibrio (menor número = más calidad)."""
    out = _out(src, ".mp4", "comprimido", out_dir)
    _run([ffmpeg_exe(), "-y", "-i", str(src), "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
          "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return out


def video_audio(src: Path, out_dir: Path | None = None) -> Path:
    out = _out(src, ".mp3", "audio", out_dir)
    _run([ffmpeg_exe(), "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    return out


def video_trim(src: Path, start: str, end: str, out_dir: Path | None = None) -> Path:
    out = _out(src, src.suffix, "recorte", out_dir)
    _run([ffmpeg_exe(), "-y", "-ss", start, "-to", end, "-i", str(src), "-c", "copy", str(out)])
    return out


def video_gif(src: Path, width: int = 640, fps: int = 12, out_dir: Path | None = None) -> Path:
    out = _out(src, ".gif", "gif", out_dir)
    _run([ffmpeg_exe(), "-y", "-i", str(src), "-vf", f"fps={fps},scale={width}:-1:flags=lanczos", str(out)])
    return out


# ---------- imagen ----------
def image_resize(src: Path, width: int = 1600, out_dir: Path | None = None) -> Path:
    from PIL import Image, ImageOps
    out = _out(src, ".jpg" if src.suffix.lower() in (".jpg", ".jpeg") else src.suffix, f"{width}px", out_dir)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        if out.suffix.lower() == ".jpg":
            im = im.convert("RGB")
        im.save(out, quality=88, optimize=True)
    return out


def image_to_webp(src: Path, out_dir: Path | None = None) -> Path:
    from PIL import Image, ImageOps
    out = _out(src, ".webp", "web", out_dir)
    with Image.open(src) as im:
        ImageOps.exif_transpose(im).save(out, "WEBP", quality=85, method=6)
    return out


# ---------- audio → texto ----------
_MODEL = None


def transcribe(src: Path, lang: str = "es", model: str = "small", out_dir: Path | None = None) -> tuple[Path, Path]:
    """Transcribe localmente (faster-whisper, CPU int8). Devuelve (.srt, .txt). El modelo se descarga una vez (~460 MB)."""
    global _MODEL
    from faster_whisper import WhisperModel
    if _MODEL is None:
        _MODEL = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = _MODEL.transcribe(str(src), language=lang, vad_filter=True)
    srt, txt = _out(src, ".srt", "subtitulos", out_dir), _out(src, ".txt", "transcripcion", out_dir)
    lines, plain = [], []
    ts = lambda s: f"{int(s // 3600):02}:{int(s % 3600 // 60):02}:{int(s % 60):02},{int(s % 1 * 1000):03}"
    for i, seg in enumerate(segments, 1):
        lines.append(f"{i}\n{ts(seg.start)} --> {ts(seg.end)}\n{seg.text.strip()}\n")
        plain.append(seg.text.strip())
    srt.write_text("\n".join(lines), encoding="utf-8")
    txt.write_text("\n".join(plain), encoding="utf-8")
    return srt, txt


def transcribe_text(src: Path, lang: str = "es") -> str:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _, txt = transcribe(src, lang, out_dir=Path(d))
        return txt.read_text(encoding="utf-8").strip()


# ---------- PDF / OCR ----------
def pdf_to_text(src: Path, out_dir: Path | None = None) -> Path:
    """Extrae el texto; en páginas escaneadas (sin texto) aplica OCR con el modelo de visión local."""
    import fitz
    from . import ai
    out = _out(src, ".txt", "texto", out_dir)
    parts = []
    with fitz.open(src) as doc:
        for n, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if len(text) < 20 and ai.available():
                png = db.DATA_DIR / "ocr-page.png"
                page.get_pixmap(dpi=150).save(png)
                text = ai.ocr_image(png)
                png.unlink(missing_ok=True)
            parts.append(f"--- página {n} ---\n{text}")
    out.write_text("\n\n".join(parts), encoding="utf-8")
    return out


def image_ocr(src: Path, out_dir: Path | None = None) -> Path:
    from . import ai
    out = _out(src, ".txt", "texto", out_dir)
    out.write_text(ai.ocr_image(src), encoding="utf-8")
    return out


# ---------- carpetas de arrastrar y soltar ----------
ACTIONS = {  # subcarpeta → (extensiones, función, descripción)
    "1-Comprimir-video": (VIDEO_EXT, lambda p, o: video_compress(p, out_dir=o)),
    "2-Extraer-audio": (VIDEO_EXT | AUDIO_EXT, lambda p, o: video_audio(p, out_dir=o)),
    "3-Transcribir-y-subtitular": (VIDEO_EXT | AUDIO_EXT, lambda p, o: transcribe(p, out_dir=o)[0]),
    "4-Imagenes-reducir-1600px": (IMAGE_EXT, lambda p, o: image_resize(p, 1600, o)),
    "5-Imagenes-a-WebP": (IMAGE_EXT, lambda p, o: image_to_webp(p, o)),
    "6-Texto-de-PDF-o-imagen": (IMAGE_EXT | {".pdf"}, lambda p, o: pdf_to_text(p, o) if p.suffix.lower() == ".pdf" else image_ocr(p, o)),
}


def ensure_folders() -> Path:
    for a in ACTIONS:
        (MEDIA_HOME / a).mkdir(parents=True, exist_ok=True)
    (MEDIA_HOME / "Salida").mkdir(exist_ok=True)
    (MEDIA_HOME / "Originales-procesados").mkdir(exist_ok=True)
    return MEDIA_HOME


def watch(min_age_s: int = 20) -> str:
    """Job: procesa lo que el usuario soltó en cada carpeta. Resultado → Salida\\; original → Originales-procesados\\."""
    ensure_folders()
    done, failed = [], []
    for action, (exts, fn) in ACTIONS.items():
        for f in sorted((MEDIA_HOME / action).iterdir()):
            if not f.is_file() or f.suffix.lower() not in exts or time.time() - f.stat().st_mtime < min_age_s:
                continue  # aún se está copiando
            try:
                fn(f, MEDIA_HOME / "Salida")
                shutil.move(str(f), str(MEDIA_HOME / "Originales-procesados" / f.name))
                done.append(f.name)
            except Exception as e:
                failed.append(f"{f.name}: {e}")
                (MEDIA_HOME / action / f"{f.name}.error.txt").write_text(str(e), encoding="utf-8")
    with db.session() as con:
        db.audit(con, "media.watch", "LOCAL_ACTION", str(MEDIA_HOME), "failed" if failed else "ok", f"{len(done)} ok, {len(failed)} fallos", actor="job")
    if failed:
        raise RuntimeError("; ".join(failed)[:400])
    return f"{len(done)} archivos procesados"


# ---------- CLI ----------
def _p(x: str) -> Path:
    p = Path(x)
    if not p.exists():
        raise typer.BadParameter(f"no existe: {x}")
    return p


@app.command("compress")
def compress_cmd(file: str, crf: int = 27) -> None:
    typer.echo(video_compress(_p(file), crf))


@app.command("audio")
def audio_cmd(file: str) -> None:
    typer.echo(video_audio(_p(file)))


@app.command("trim")
def trim_cmd(file: str, start: str, end: str) -> None:
    """Recorta sin recodificar. Tiempos como 00:01:30."""
    typer.echo(video_trim(_p(file), start, end))


@app.command("gif")
def gif_cmd(file: str, width: int = 640) -> None:
    typer.echo(video_gif(_p(file), width))


@app.command("transcribe")
def transcribe_cmd(file: str, lang: str = "es", model: str = "small") -> None:
    srt, txt = transcribe(_p(file), lang, model)
    typer.echo(f"{srt}\n{txt}")


@app.command("resize")
def resize_cmd(file: str, width: int = 1600) -> None:
    typer.echo(image_resize(_p(file), width))


@app.command("webp")
def webp_cmd(file: str) -> None:
    typer.echo(image_to_webp(_p(file)))


@app.command("text")
def text_cmd(file: str) -> None:
    """Texto de un PDF o imagen (OCR local)."""
    p = _p(file)
    typer.echo(pdf_to_text(p) if p.suffix.lower() == ".pdf" else image_ocr(p))


@app.command("editors")
def editors_cmd() -> None:
    """Editores gráficos instalados y cuáles faltan."""
    for e in editors():
        typer.echo(f"{'✔' if e['path'] else '·'} {e['key']:<8} {e['label']}")


@app.command("edit")
def edit_cmd(editor: str = typer.Argument(..., help="video | imagen | vector | mejorar | resolve"), file: str = typer.Argument(None)) -> None:
    """Abre un editor (opcionalmente con un archivo)."""
    try:
        typer.echo(f"Abriendo {open_editor(editor, file)}…")
    except RuntimeError as e:
        raise typer.BadParameter(str(e))


@app.command("folders")
def folders_cmd() -> None:
    """Crea (si faltan) las carpetas de arrastrar y soltar."""
    typer.echo(str(ensure_folders()))
