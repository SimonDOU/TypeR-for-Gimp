"""Modelos de datos puros (sin dependencias de GIMP ni GTK).

Todo lo que hay aquí se puede probar con `python -m unittest` fuera de GIMP.
"""
from __future__ import annotations

__version__ = "0.4.0"   # debe coincidir con typer_core.__version__

import re
from dataclasses import dataclass, field, fields, asdict, replace
from typing import Dict, List, Optional, Set, Tuple

DEFAULT_IGNORE_SPEC = "1, 2, 3, 4, 5, 6, 7, 8, 9, 0, *, -, #"
_LEGACY_IGNORE_SPECS = {"1, 2, 3... * - _"}   # valor por defecto de la v1 (migración)


# --------------------------------------------------------------------------- #
#  Estilos tipográficos
# --------------------------------------------------------------------------- #
@dataclass
class TextStyle:
    name: str
    font: str = "Sans-serif"          # familia tal como la lista GIMP/fontconfig
    size: float = 32.0                # px
    line_spacing_pct: float = 0.0     # % del tamaño; negativo = más compacto
    bold: bool = False
    italic: bool = False
    uppercase: bool = False
    fill_color: str = "#000000"
    stroke_enabled: bool = True
    stroke_color: str = "#ffffff"
    stroke_width: int = 4             # px de contorno exterior
    justification: str = "center"     # left | center | right
    fit_ratio: float = 0.85           # ancho máx. del texto = ratio * ancho de la selección

    def transform(self, text: str) -> str:
        return text.upper() if self.uppercase else text

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TextStyle":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def default_styles() -> Dict[str, TextStyle]:
    styles = [
        TextStyle("Diálogo"),
        TextStyle("Pensamiento", italic=True, size=30.0),
        TextStyle("Cartela", size=28.0, stroke_enabled=False, fit_ratio=0.92),
        TextStyle("Grito", bold=True, uppercase=True, size=44.0,
                  line_spacing_pct=-10.0, stroke_width=5),
    ]
    return {s.name: s for s in styles}


# --------------------------------------------------------------------------- #
#  Filtro de líneas ignoradas
# --------------------------------------------------------------------------- #
_LINE_BREAKS = re.compile(r"\r\n|\r|\n|\u2029")   # mismos separadores que GtkTextBuffer


def split_lines(text: str) -> List[str]:
    """Divide igual que el bloc de notas GTK, para que nº de línea == nº de línea del buffer."""
    return _LINE_BREAKS.split(text)


class IgnoreFilter:
    """Decide qué líneas del guion se OMITEN por completo.

    `spec` es una lista separada por comas (o espacios), p. ej.
    ``1, 2, 3, 4, 5, 6, 7, 8, 9, 0, *, -, #``. Si una línea empieza (ignorando
    espacios y BOM) por cualquiera de esos caracteres/textos, se salta entera:
    no se inserta, no cuenta en el progreso y el cursor pasa a la siguiente.
    Cada token es un prefijo literal, así que también valen ``//`` o ``[SFX]``.
    Las líneas en blanco también se omiten.
    """

    def __init__(self, spec: str):
        self.spec = spec
        tokens = {t for t in re.split(r"[,\s]+", spec.strip()) if t}
        self.prefixes = tuple(sorted(tokens, key=len, reverse=True))

    @staticmethod
    def _normalize(line: str) -> str:
        return line.lstrip().lstrip("\ufeff").lstrip()

    def is_ignored(self, line: str) -> bool:
        """True si la línea es NO vacía y empieza por un prefijo configurado."""
        text = self._normalize(line)
        return bool(text) and bool(self.prefixes) and text.startswith(self.prefixes)

    def clean(self, line: str) -> str:
        """Texto a insertar, o "" si la línea debe omitirse (vacía o ignorada)."""
        text = self._normalize(line).rstrip()
        if not text or self.is_ignored(text):
            return ""
        return text


# --------------------------------------------------------------------------- #
#  Estado de la aplicación (se persiste en JSON)
# --------------------------------------------------------------------------- #
@dataclass
class ScriptLine:
    index: int   # nº de línea (base 0) en el bloc de notas
    raw: str
    text: str    # texto ya limpio


@dataclass
class AppState:
    script_text: str = ""
    cursor: int = 0                   # línea del bloc a partir de la cual se busca la siguiente
    ignore_spec: str = DEFAULT_IGNORE_SPEC
    active_style: str = ""
    keep_selection: bool = True
    apply_on_click: bool = True       # clic en un estilo = elegirlo Y aplicarlo a la capa de texto seleccionada
    styles: Dict[str, TextStyle] = field(default_factory=default_styles)

    def __post_init__(self):
        if not self.styles:
            self.styles = default_styles()
        if self.active_style not in self.styles:
            self.active_style = next(iter(self.styles))
        self.cursor = max(0, int(self.cursor))

    # ---- estilos ---------------------------------------------------------- #
    def style(self) -> TextStyle:
        return self.styles[self.active_style]

    def rename_style(self, old: str, new: str) -> None:
        """Renombra conservando el orden. ValueError si el nombre ya existe."""
        if old not in self.styles:
            raise ValueError(f"No existe el estilo «{old}».")
        if new != old and new in self.styles:
            raise ValueError(f"Ya existe un estilo llamado «{new}».")
        self.styles = {
            (new if k == old else k): (replace(v, name=new) if k == old else v)
            for k, v in self.styles.items()
        }
        if self.active_style == old:
            self.active_style = new

    # ---- guion ------------------------------------------------------------ #
    def _lines(self) -> List[str]:
        return split_lines(self.script_text)

    def ignored_indices(self) -> Set[int]:
        """Índices de líneas NO vacías que el filtro omite (para tacharlas en la UI)."""
        flt = IgnoreFilter(self.ignore_spec)
        return {i for i, l in enumerate(self._lines()) if flt.is_ignored(l)}

    def current_entry(self) -> Optional[ScriptLine]:
        """Primera línea válida desde el cursor: salta en blanco e ignoradas."""
        lines, flt = self._lines(), IgnoreFilter(self.ignore_spec)
        for i in range(self.cursor, len(lines)):
            text = flt.clean(lines[i])
            if text:
                return ScriptLine(i, lines[i], text)
        return None

    def advance(self) -> bool:
        """Pasa a la línea válida siguiente (puede dejar el guion «terminado»)."""
        entry = self.current_entry()
        if entry is None:
            return False
        self.cursor = entry.index + 1
        return True

    def retreat(self) -> bool:
        lines, flt = self._lines(), IgnoreFilter(self.ignore_spec)
        entry = self.current_entry()
        base = entry.index if entry else len(lines)
        for i in range(base - 1, -1, -1):
            if flt.clean(lines[i]):
                self.cursor = i
                return True
        return False

    def move(self, delta: int) -> bool:
        """Como ↑/↓: salta a la válida anterior/siguiente SIN pasar del final."""
        if delta < 0:
            return self.retreat()
        entry = self.current_entry()
        if entry is None:
            return False
        lines, flt = self._lines(), IgnoreFilter(self.ignore_spec)
        for i in range(entry.index + 1, len(lines)):
            if flt.clean(lines[i]):
                self.cursor = i
                return True
        return False

    def select_line(self, index: int) -> Optional[ScriptLine]:
        """Activa la línea `index`. Si está ignorada/vacía, la válida siguiente;
        si no queda ninguna después, la última válida. Devuelve la activa."""
        self.cursor = min(max(0, index), len(self._lines()))
        entry = self.current_entry()
        if entry is None and self.retreat():
            entry = self.current_entry()
        if entry is not None:
            self.cursor = entry.index
        return entry

    def progress(self) -> Tuple[int, int]:
        """(líneas válidas ya procesadas, total de líneas válidas)."""
        lines, flt = self._lines(), IgnoreFilter(self.ignore_spec)
        valid = [i for i, l in enumerate(lines) if flt.clean(l)]
        return sum(1 for i in valid if i < self.cursor), len(valid)

    # ---- (de)serialización ------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "script_text": self.script_text,
            "cursor": self.cursor,
            "ignore_spec": self.ignore_spec,
            "active_style": self.active_style,
            "keep_selection": self.keep_selection,
            "apply_on_click": self.apply_on_click,
            "styles": [s.to_dict() for s in self.styles.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppState":
        styles: Dict[str, TextStyle] = {}
        for item in data.get("styles", []):
            if isinstance(item, dict) and item.get("name"):
                style = TextStyle.from_dict(item)
                styles[style.name] = style
        spec = str(data.get("ignore_spec", DEFAULT_IGNORE_SPEC))
        if spec.strip() in _LEGACY_IGNORE_SPECS:      # la v1 «quitaba» prefijos; ahora se omiten líneas
            spec = DEFAULT_IGNORE_SPEC
        return cls(
            script_text=str(data.get("script_text", "")),
            cursor=int(data.get("cursor", 0)),
            ignore_spec=spec,
            active_style=str(data.get("active_style", "")),
            keep_selection=bool(data.get("keep_selection", True)),
            apply_on_click=bool(data.get("apply_on_click", True)),
            styles=styles,
        )
