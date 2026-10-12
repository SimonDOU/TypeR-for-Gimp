"""Operaciones sobre GIMP 3.x (API Python/PyGObject).

Resultado de cada inserción — un grupo de capas, todo en UN paso de deshacer:

    TypeR · L12            (grupo)
    ├── <texto>            (capa de texto, SIGUE SIENDO EDITABLE)
    └── Contorno           (capa raster con el trazo blanco, debajo del texto)

GIMP no tiene «trazo de capa» nativo para texto, por eso el contorno es una
capa aparte generada a partir del alfa del texto + «Agrandar selección».
Si editas el texto después, usa «Aplicar estilo» para regenerar el contorno.
"""
from __future__ import annotations

__version__ = "0.4.0"   # debe coincidir con typer_core.__version__

import html
import math
import re
import sys
import traceback
from contextlib import contextmanager
from typing import List, Optional, Tuple

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp  # noqa: E402

from .models import TextStyle  # noqa: E402

__all__ = ["TypesetError", "NoTextLayerSelected", "Typesetter", "selection_bounds",
           "resolve_font", "GROUP_PREFIX", "STROKE_NAME"]

GROUP_PREFIX = "TypeR"
STROKE_NAME = "Contorno"

_JUSTIFY = {
    "left": Gimp.TextJustification.LEFT,
    "center": Gimp.TextJustification.CENTER,
    "right": Gimp.TextJustification.RIGHT,
}


class TypesetError(Exception):
    """Error «esperable» (sin selección, guion vacío…) con mensaje para el usuario."""

    expected = True          # el plugin no imprime traceback para estos


class NoTextLayerSelected(TypesetError):
    """No hay ninguna capa de texto seleccionada (el panel puede tratarlo como aviso suave)."""


# --------------------------------------------------------------------------- #
#  Helpers de API (aíslan las diferencias entre versiones de GIMP 3.x)
# --------------------------------------------------------------------------- #
def _pixel_unit():
    try:
        return Gimp.Unit.pixel()
    except AttributeError:          # builds de desarrollo 2.99.x
        return Gimp.Unit.PIXEL


def _color(hex_str: str):
    return Gegl.Color.new(hex_str)


def _lookup_font(name: str):
    try:
        getter = getattr(Gimp.Font, "get_by_name", None)
        if getter is not None:
            font = getter(name)
            if font is not None:
                return font
    except Exception:
        pass
    try:
        for font in Gimp.fonts_get_list("^" + re.escape(name) + "$") or []:
            return font
    except Exception:
        pass
    return None


def resolve_font(style: TextStyle):
    """Devuelve (Gimp.Font, usar_markup, aviso).

    1) Si hay negrita/cursiva, intenta la cara real («Familia Bold Italic»).
    2) Si no existe, usa la familia base y simula con <b>/<i> (Pango).
    3) Si la familia no existe, cae a la fuente activa de GIMP y avisa.
    """
    suffix = " ".join(p for p, on in (("Bold", style.bold), ("Italic", style.italic)) if on)
    candidates = ([f"{style.font} {suffix}"] if suffix else []) + [style.font]
    for i, name in enumerate(candidates):
        font = _lookup_font(name)
        if font is not None:
            real_face = bool(suffix) and i == 0
            return font, bool(suffix) and not real_face, None
    warning = f"Fuente «{style.font}» no encontrada; se usó la fuente activa de GIMP."
    return Gimp.context_get_font(), bool(suffix), warning


def selection_bounds(image) -> Tuple[int, int, int, int]:
    # GIMP 3.x devuelve (non_empty, x1, y1, x2, y2); [-5:] tolera un posible valor extra delante.
    non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[-5:]
    if not non_empty or x2 <= x1 or y2 <= y1:
        raise TypesetError(
            "No hay una selección activa. Selecciona el interior del globo "
            "(varita mágica, lazo…) antes de insertar."
        )
    return x1, y1, x2, y2


def _offsets(layer) -> Tuple[int, int]:
    res = layer.get_offsets()          # (bool, x, y) según la versión
    return int(res[-2]), int(res[-1])


def _plain_text(layer) -> str:
    text = layer.get_text()
    if text is None:                   # la capa usa markup
        markup = layer.get_markup() or ""
        text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return text


def _markup(text: str, style: TextStyle) -> str:
    out = html.escape(text, quote=False)
    if style.bold:
        out = f"<b>{out}</b>"
    if style.italic:
        out = f"<i>{out}</i>"
    return out


# --------------------------------------------------------------------------- #
#  Typesetter
# --------------------------------------------------------------------------- #
class Typesetter:
    def __init__(self, image):
        if image is None or not image.is_valid():
            raise TypesetError("No hay ninguna imagen abierta.")
        self.image = image

    # ---- API pública ------------------------------------------------------ #
    def insert_line(self, text: str, style: TextStyle, label: str,
                    keep_selection: bool = True) -> List[str]:
        """Inserta `text` centrado en la selección actual. Devuelve avisos."""
        x1, y1, x2, y2 = selection_bounds(self.image)      # falla ANTES de tocar nada
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        max_w = (x2 - x1) * style.fit_ratio
        text = style.transform(text)
        if not text.strip():                                 # el filtro ya lo evita; red de seguridad
            raise TypesetError("La línea activa está vacía; no se creó ninguna capa.")
        font, use_markup, warning = resolve_font(style)

        try:
            with self._guarded(keep_selection):
                group = self._new_group(f"{GROUP_PREFIX} · {label}")
                self.image.insert_layer(group, None, 0)
                layer = Gimp.TextLayer.new(self.image, text, font, style.size, _pixel_unit())
                if layer is None:
                    raise TypesetError("GIMP no pudo crear la capa de texto.")
                self.image.insert_layer(layer, group, 0)
                self._configure(layer, text, style, font, use_markup)
                self._fit_width(layer, text, max_w)
                self._center_on(layer, cx, cy)
                if style.stroke_enabled and style.stroke_width > 0:
                    self._add_stroke(group, layer, style)
        except TypesetError:
            raise
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            raise TypesetError(f"Error de GIMP: {exc}") from exc
        return [warning] if warning else []

    def restyle_selected(self, style: TextStyle, keep_selection: bool = True) -> int:
        """Reaplica `style` a las capas de texto seleccionadas. Devuelve cuántas."""
        targets, seen = [], set()
        for layer in self.image.get_selected_layers():
            text_layer = self._resolve_text_layer(layer)
            if text_layer is not None and text_layer.get_id() not in seen:
                seen.add(text_layer.get_id())
                targets.append(text_layer)
        if not targets:
            raise NoTextLayerSelected("Selecciona una capa de texto (o su grupo «TypeR») en el panel de capas.")
        try:
            with self._guarded(keep_selection):
                for layer in targets:
                    self._restyle(layer, style)
        except TypesetError:
            raise
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            raise TypesetError(f"Error de GIMP: {exc}") from exc
        return len(targets)

    # ---- internos --------------------------------------------------------- #
    @contextmanager
    def _guarded(self, keep_selection: bool):
        """Un solo paso de deshacer + contexto aislado + selección restaurada."""
        image = self.image
        Gimp.context_push()
        image.undo_group_start()
        saved = Gimp.Selection.save(image) if keep_selection else None
        try:
            yield
        finally:
            try:
                if saved is not None:
                    image.select_item(Gimp.ChannelOps.REPLACE, saved)
                    image.remove_channel(saved)
                else:
                    Gimp.Selection.none(image)
            finally:
                image.undo_group_end()
                Gimp.context_pop()
                Gimp.displays_flush()

    def _new_group(self, name: str):
        try:
            return Gimp.GroupLayer.new(self.image, name)
        except TypeError:               # firma antigua sin nombre
            group = Gimp.GroupLayer.new(self.image)
            group.set_name(name)
            return group

    @staticmethod
    def _configure(layer, text: str, style: TextStyle, font, use_markup: bool) -> None:
        unit = _pixel_unit()
        layer.set_font(font)
        layer.set_font_size(style.size, unit)
        layer.set_antialias(True)
        layer.set_justification(_JUSTIFY.get(style.justification, Gimp.TextJustification.CENTER))
        layer.set_line_spacing(style.size * style.line_spacing_pct / 100.0)
        layer.set_color(_color(style.fill_color))
        if use_markup:
            layer.set_markup(_markup(text, style))
        else:
            layer.set_text(text)

    @staticmethod
    def _fit_width(layer, text: str, max_w: float) -> None:
        """Si el texto es más ancho que el globo, fija una caja y deja que haga wrap.

        HEURÍSTICA: estima las líneas resultantes como ceil(ancho/max_w).
        Punto de mejora: medir con Pango el alto real del texto ya envuelto.
        """
        w, h = layer.get_width(), layer.get_height()
        if max_w <= 0 or w <= max_w:
            return
        lines = text.count("\n") + 1
        est_lines = lines + math.ceil(w / max_w) - 1
        layer.resize(int(max_w), int(math.ceil(h / lines * est_lines)))

    @staticmethod
    def _center_on(layer, cx: float, cy: float) -> None:
        layer.set_offsets(int(round(cx - layer.get_width() / 2.0)),
                          int(round(cy - layer.get_height() / 2.0)))

    def _add_stroke(self, group, text_layer, style: TextStyle) -> None:
        image, sw = self.image, int(style.stroke_width)
        pad = sw + 2
        image.select_item(Gimp.ChannelOps.REPLACE, text_layer)   # alfa del texto
        Gimp.Selection.grow(image, sw)
        ox, oy = _offsets(text_layer)
        stroke = Gimp.Layer.new(
            image, STROKE_NAME,
            text_layer.get_width() + 2 * pad, text_layer.get_height() + 2 * pad,
            Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL,
        )
        image.insert_layer(stroke, group, 1)                     # debajo del texto
        stroke.set_offsets(ox - pad, oy - pad)
        stroke.fill(Gimp.FillType.TRANSPARENT)
        Gimp.context_set_foreground(_color(style.stroke_color))
        stroke.edit_fill(Gimp.FillType.FOREGROUND)
        Gimp.Selection.none(image)

    @staticmethod
    def _resolve_text_layer(layer) -> Optional["Gimp.TextLayer"]:
        if isinstance(layer, Gimp.TextLayer):
            return layer
        if layer.is_group() and layer.get_name().startswith(GROUP_PREFIX):
            for child in layer.get_children():
                if isinstance(child, Gimp.TextLayer):
                    return child
        return None

    def _restyle(self, layer, style: TextStyle) -> None:
        image = self.image
        w0, h0 = layer.get_width(), layer.get_height()
        ox, oy = _offsets(layer)
        cx, cy = ox + w0 / 2.0, oy + h0 / 2.0                    # conserva el centro actual

        font, use_markup, _warn = resolve_font(style)
        self._configure(layer, style.transform(_plain_text(layer)), style, font, use_markup)
        self._center_on(layer, cx, cy)

        parent = layer.get_parent()
        group = parent if (parent is not None and parent.get_name().startswith(GROUP_PREFIX)) else None
        if group is not None:                                    # quita el contorno viejo
            for child in list(group.get_children()):
                if child.get_name() == STROKE_NAME:
                    image.remove_layer(child)

        wants_stroke = style.stroke_enabled and style.stroke_width > 0
        if group is None and wants_stroke:                       # capa de texto «suelta»: agrúpala
            group = self._new_group(f"{GROUP_PREFIX} · {layer.get_name()}")
            image.insert_layer(group, parent, image.get_item_position(layer))
            image.reorder_item(layer, group, 0)
        if group is not None and wants_stroke:
            self._add_stroke(group, layer, style)
