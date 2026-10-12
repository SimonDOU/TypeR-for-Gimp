"""Panel vertical GTK3 al estilo TypeR.

Distribución (de arriba abajo):

    [ Insertar texto y centrar ]  [⚙]     ← acción principal + configuración
    (mensaje de estado, solo si hay)
    ┌ Guion ──────────── 2/2 ◀ ▶ ✎ ┐     ← Gtk.Paned: el divisor se arrastra
    │  bloc de notas                │
    │  ▶ texto que se insertará…    │
    ├───────────────────────────────┤  ←  divisor
    │ Estilos                  + ✕  │
    │ (○ Diálogo ✔✎) (○ Grito ✔✎)   │     ← rejilla de 2 columnas; ✔ = aplicar
    └───────────────────────────────┘

* ✎ de un estilo → ventana «Editar estilo» (fuente, tamaño, contorno…).
* ⚙ → ventana «Configuración» (filtro de líneas, imagen destino, opciones).
* Ventana no modal; el estado se comparte con los atajos vía StateStore y se
  vigila con Gio.FileMonitor.

Bloc de notas — dos modos:
  · Navegación (por defecto): clic = elegir línea · ↑/↓ = moverse · Enter = insertar.
  · Edición (botón ✎): texto libre para pegar/corregir el guion.
"""
from __future__ import annotations

__version__ = "0.4.0"   # debe coincidir con typer_core.__version__

import html
import traceback
from contextlib import contextmanager
from dataclasses import replace

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gimp, GimpUi, Gio, GLib, Gtk, Pango  # noqa: E402

from . import actions  # noqa: E402
from .models import TextStyle  # noqa: E402
from .store import StateStore  # noqa: E402
from .typesetter import NoTextLayerSelected, TypesetError  # noqa: E402

V, H = Gtk.Orientation.VERTICAL, Gtk.Orientation.HORIZONTAL

_CSS = b"""
button.typer-primary {
    background-image: none; background-color: #1f6feb; color: #ffffff;
    border: 1px solid #1858bd; border-radius: 6px; padding: 8px 6px;
    font-weight: bold; text-shadow: none; box-shadow: none;
}
button.typer-primary label { color: #ffffff; }
button.typer-primary:hover { background-color: #3b82f6; }
button.typer-primary:active { background-color: #1858bd; }
button.typer-primary:disabled { background-color: #7fa8e8; }

button.typer-small { padding: 1px 7px; min-height: 22px; min-width: 0px; }

.style-pill {
    border: 1px solid rgba(128,128,128,0.45); border-radius: 12px;
    padding: 0px 2px 0px 6px; min-height: 24px;
}
.style-pill.active { border-color: #1f6feb; background-color: rgba(31,111,235,0.14); }
.style-pill radiobutton { padding: 0px; min-height: 0px; }
button.typer-mini {
    padding: 0px 2px; margin: 0px; min-width: 18px; min-height: 18px;
    border: none; background-image: none; background-color: transparent; box-shadow: none;
}
button.typer-mini:hover { background-color: rgba(128,128,128,0.25); border-radius: 9px; }
"""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _rgba(hex_str: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(hex_str)
    return c


def _hex(c: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(round(v * 255)) for v in (c.red, c.green, c.blue)))


def _unique(name: str, taken) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def _iter_at_line(buf: Gtk.TextBuffer, line: int) -> Gtk.TextIter:
    res = buf.get_iter_at_line(line)
    return res[-1] if isinstance(res, tuple) else res


def _icon_widget(names, fallback_text: str) -> Gtk.Widget:
    """Primer icono del tema que exista; si ninguno, un glifo de texto.

    Evita el icono genérico de «imagen no encontrada» (en GIMP sale el Wilber)
    cuando el tema activo no trae un nombre concreto.
    """
    theme = Gtk.IconTheme.get_default()
    for name in names:
        if theme.has_icon(name):
            return Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU)
    return Gtk.Label(label=fallback_text)


def _icon_button(names, fallback: str, tip: str, css_class: str | None = None) -> Gtk.Button:
    btn = Gtk.Button()
    btn.add(_icon_widget(names, fallback))
    btn.set_tooltip_text(tip)
    if css_class:
        btn.get_style_context().add_class(css_class)
    return btn


def _style_summary(s: TextStyle) -> str:
    bits = [s.font, f"{s.size:g} px"]
    bits += [t for t, on in (("B", s.bold), ("I", s.italic), ("AA", s.uppercase)) if on]
    bits.append(f"contorno {s.stroke_width} px" if s.stroke_enabled and s.stroke_width else "sin contorno")
    return " · ".join(bits)


def _name_markup(s: TextStyle) -> str:
    """El nombre del estilo escrito con SU tipografía (negrita/cursiva incluidas)."""
    attrs = f'font_family="{html.escape(s.font, quote=True)}" size="10240"'   # 10 pt
    if s.bold:
        attrs += ' weight="bold"'
    if s.italic:
        attrs += ' style="italic"'
    return f"<span {attrs}>{html.escape(s.transform(s.name))}</span>"


# --------------------------------------------------------------------------- #
#  Panel
# --------------------------------------------------------------------------- #
class TypeRPanel(Gtk.Window):
    def __init__(self, store: StateStore, image):
        super().__init__(title="TypeR · GIMP")
        self.store = store
        self.state = store.load()
        self._image = image
        self._images: list = []
        self._known_image_ids: set = set()
        self._loading = 0
        self._save_timer = 0
        self._reload_source = 0
        self._status_timer = 0
        self._edit_mode = False
        self._card_names: list = []
        self._cards: dict = {}

        self.set_default_size(380, 780)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(_CSS)
            # USER > temas de GIMP/GTK: garantiza que el botón azul gane.
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except GLib.Error as exc:
            print(f"typer-gimp: CSS no cargado: {exc}")

        root = Gtk.Box(orientation=V, spacing=6)
        root.set_border_width(6)
        self.add(root)

        root.pack_start(self._build_header(), False, False, 0)

        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        self.status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status.set_no_show_all(True)              # oculto mientras no haya mensaje
        root.pack_start(self.status, False, False, 0)

        # Divisor deslizante: guion (arriba) ⇄ estilos (abajo).
        paned = Gtk.Paned(orientation=V)
        paned.set_wide_handle(True)
        paned.pack1(self._build_script_section(), True, False)
        paned.pack2(self._build_style_section(), True, False)
        paned.set_position(215)
        root.pack_start(paned, True, True, 0)

        self._build_editor_dialog()
        self._build_settings_dialog()

        self.connect("delete-event", self._on_delete)
        self.connect("focus-in-event", lambda *_: self._refresh_images())

        self._monitor = Gio.File.new_for_path(store.directory).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_file_changed)

        self._refresh_images()
        self._sync_widgets()
        self._set_edit_mode(not self.state.script_text.strip())   # guion vacío → a pegar

    # ------------------------------------------------------------------ #
    #  Cabecera: acción principal + configuración
    # ------------------------------------------------------------------ #
    def _build_header(self):
        row = Gtk.Box(orientation=H, spacing=4)
        self.btn_insert = Gtk.Button.new_with_label("Insertar texto y centrar")
        self.btn_insert.get_style_context().add_class("typer-primary")     # azul (ver _CSS)
        self.btn_insert.set_tooltip_text(
            "Atajo global: asigna «TypeR: Insertar siguiente línea» en\n"
            "Editar ▸ Atajos de teclado (funciona con GIMP enfocado).")
        self.btn_insert.connect("clicked", lambda *_: self._run_action(actions.insert_next))
        row.pack_start(self.btn_insert, True, True, 0)

        gear = _icon_button(["emblem-system-symbolic", "preferences-system-symbolic",
                             "applications-system-symbolic"], "⚙", "Configuración")
        gear.connect("clicked", lambda *_: self._open_dialog(self.settings_dlg))
        row.pack_start(gear, False, False, 0)
        return row

    # ------------------------------------------------------------------ #
    #  Panel superior: bloc de notas / guion
    # ------------------------------------------------------------------ #
    def _build_script_section(self):
        box = Gtk.Box(orientation=V, spacing=3)

        head = Gtk.Box(orientation=H, spacing=3)
        title = Gtk.Label(label="<b>Guion</b>", use_markup=True, xalign=0)
        title.set_tooltip_text("Clic: elegir línea · ↑/↓: moverse · Enter: insertar\n"
                               "Líneas tachadas = ignoradas por el filtro")
        head.pack_start(title, True, True, 0)
        self.counter = Gtk.Label(xalign=1)
        head.pack_start(self.counter, False, False, 4)
        for label, tip, cb in (
            ("◀", "Línea válida anterior (↑)", lambda *_: self._move(-1)),
            ("▶", "Saltar esta línea sin insertar", self._on_skip),
        ):
            btn = Gtk.Button.new_with_label(label)
            btn.get_style_context().add_class("typer-small")
            btn.set_tooltip_text(tip)
            btn.connect("clicked", cb)
            head.pack_start(btn, False, False, 0)
        self.edit_btn = Gtk.ToggleButton()
        self.edit_btn.add(_icon_widget(["document-edit-symbolic", "gtk-edit"], "✎"))
        self.edit_btn.get_style_context().add_class("typer-small")
        self.edit_btn.set_tooltip_text("Editar el guion (pegar o corregir texto)")
        self.edit_btn.connect("toggled", self._on_edit_toggled)
        head.pack_start(self.edit_btn, False, False, 0)
        box.pack_start(head, False, False, 0)

        self.textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=6, right_margin=6, top_margin=4, bottom_margin=4)
        self.buffer = self.textview.get_buffer()
        # El orden de creación fija la prioridad: la última (active) gana.
        self.tag_ignored = self.buffer.create_tag("ignored", foreground="#9a9a9a", strikethrough=True)
        self.tag_done = self.buffer.create_tag("done", foreground="#8a8a8a")
        self.tag_active = self.buffer.create_tag(
            "active", background="#f6d365", foreground="#1a1a1a", weight=Pango.Weight.BOLD)
        self.buffer.connect("changed", self._on_script_edited)
        self.textview.connect("button-press-event", self._on_textview_press)
        self.textview.connect("key-press-event", self._on_textview_key)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(40)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(self.textview)
        box.pack_start(scroller, True, True, 0)

        # Una sola línea con elipsis: un texto largo NUNCA debe ensanchar la ventana.
        self.preview = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 max_width_chars=24, single_line_mode=True)
        box.pack_start(self.preview, False, False, 0)
        return box

    # ------------------------------------------------------------------ #
    #  Panel inferior: estilos (rejilla de 2 columnas)
    # ------------------------------------------------------------------ #
    def _build_style_section(self):
        box = Gtk.Box(orientation=V, spacing=3)

        head = Gtk.Box(orientation=H, spacing=3)
        head.pack_start(Gtk.Label(label="<b>Estilos</b>", use_markup=True, xalign=0), True, True, 0)
        btn_new = _icon_button(["list-add-symbolic"], "+", "Nuevo estilo (copia del seleccionado)",
                               "typer-small")
        btn_new.connect("clicked", self._on_new_style)
        btn_del = _icon_button(["edit-delete-symbolic", "user-trash-symbolic"], "✕",
                               "Eliminar el estilo seleccionado", "typer-small")
        btn_del.connect("clicked", self._on_delete_style)
        head.pack_start(btn_new, False, False, 0)
        head.pack_start(btn_del, False, False, 0)
        box.pack_start(head, False, False, 0)

        self.cards = Gtk.FlowBox()
        self.cards.set_selection_mode(Gtk.SelectionMode.NONE)
        self.cards.set_min_children_per_line(2)
        self.cards.set_max_children_per_line(2)
        self.cards.set_homogeneous(True)
        self.cards.set_column_spacing(4)
        self.cards.set_row_spacing(3)
        self.cards.set_valign(Gtk.Align.START)          # filas a altura natural, sin estirarse
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_min_content_height(40)
        sc.set_vexpand(True)                            # todo el espacio libre es para los estilos
        sc.add(self.cards)
        box.pack_start(sc, True, True, 0)
        return box

    # ------------------------------------------------------------------ #
    #  Ventana «Editar estilo» (no modal; se actualiza al cambiar de estilo)
    # ------------------------------------------------------------------ #
    def _build_editor_dialog(self):
        dlg = Gtk.Dialog(title="Editar estilo", transient_for=self, modal=False,
                         destroy_with_parent=True)
        dlg.add_button("Cerrar", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _r: d.hide())
        dlg.connect("delete-event", lambda d, _e: d.hide_on_delete())
        area = dlg.get_content_area()
        area.set_border_width(10)
        area.set_spacing(8)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        area.pack_start(grid, True, True, 0)

        self.w_name = Gtk.Entry()
        self.w_name.set_tooltip_text("Nombre del estilo (Enter para confirmar)")
        self.w_name.connect("activate", self._commit_name)
        self.w_name.connect("focus-out-event", lambda *_: self._commit_name() or False)
        self.w_font = Gtk.FontButton()
        self.w_font.set_show_size(False)
        self.w_font.set_use_font(True)
        self.w_size = Gtk.SpinButton.new_with_range(6, 400, 1)
        self.w_lead = Gtk.SpinButton.new_with_range(-50, 100, 1)
        self.w_bold = Gtk.ToggleButton(label="B")
        self.w_italic = Gtk.ToggleButton(label="I")
        self.w_upper = Gtk.ToggleButton(label="AA")
        self.w_fill = Gtk.ColorButton()
        self.w_stroke_on = Gtk.CheckButton(label="Contorno")
        self.w_stroke_color = Gtk.ColorButton()
        self.w_stroke_w = Gtk.SpinButton.new_with_range(0, 40, 1)
        self.w_fit = Gtk.SpinButton.new_with_range(30, 100, 1)
        self.w_bold.set_tooltip_text("Negrita")
        self.w_italic.set_tooltip_text("Cursiva")
        self.w_upper.set_tooltip_text("Todo en MAYÚSCULAS")
        self.w_fit.set_tooltip_text("Ancho máximo del texto como % del ancho de la selección")

        def label(text):
            return Gtk.Label(label=text, xalign=0)

        grid.attach(label("Nombre"), 0, 0, 1, 1)
        grid.attach(self.w_name, 1, 0, 3, 1)
        grid.attach(label("Fuente"), 0, 1, 1, 1)
        grid.attach(self.w_font, 1, 1, 3, 1)
        grid.attach(label("Tamaño px"), 0, 2, 1, 1)
        grid.attach(self.w_size, 1, 2, 1, 1)
        grid.attach(label("Interlín. %"), 2, 2, 1, 1)
        grid.attach(self.w_lead, 3, 2, 1, 1)
        fmt = Gtk.Box(orientation=H, spacing=2)
        for w in (self.w_bold, self.w_italic, self.w_upper):
            fmt.pack_start(w, False, False, 0)
        grid.attach(label("Formato"), 0, 3, 1, 1)
        grid.attach(fmt, 1, 3, 1, 1)
        grid.attach(label("Relleno"), 2, 3, 1, 1)
        grid.attach(self.w_fill, 3, 3, 1, 1)
        grid.attach(self.w_stroke_on, 0, 4, 1, 1)
        grid.attach(self.w_stroke_color, 1, 4, 1, 1)
        grid.attach(label("Grosor px"), 2, 4, 1, 1)
        grid.attach(self.w_stroke_w, 3, 4, 1, 1)
        grid.attach(label("Ajuste globo %"), 0, 5, 1, 1)
        grid.attach(self.w_fit, 1, 5, 1, 1)

        for widget, signal in (
            (self.w_font, "font-set"), (self.w_size, "value-changed"), (self.w_lead, "value-changed"),
            (self.w_bold, "toggled"), (self.w_italic, "toggled"), (self.w_upper, "toggled"),
            (self.w_fill, "color-set"), (self.w_stroke_on, "toggled"), (self.w_stroke_color, "color-set"),
            (self.w_stroke_w, "value-changed"), (self.w_fit, "value-changed"),
        ):
            widget.connect(signal, self._on_style_edited)
        self.editor_dlg = dlg

    # ------------------------------------------------------------------ #
    #  Ventana «Configuración» (no modal: el tachado del guion se ve en vivo)
    # ------------------------------------------------------------------ #
    def _build_settings_dialog(self):
        dlg = Gtk.Dialog(title="Configuración · TypeR", transient_for=self, modal=False,
                         destroy_with_parent=True)
        dlg.add_button("Cerrar", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _r: d.hide())
        dlg.connect("delete-event", lambda d, _e: d.hide_on_delete())
        area = dlg.get_content_area()
        area.set_border_width(10)
        area.set_spacing(8)

        area.pack_start(Gtk.Label(label="Ignorar líneas que empiecen por:", xalign=0), False, False, 0)
        self.entry_ignore = Gtk.Entry(placeholder_text="1, 2, 3, 4, 5, 6, 7, 8, 9, 0, *, -, #")
        self.entry_ignore.set_tooltip_text(
            "Lista separada por comas. Si una línea del guion empieza por alguno\n"
            "de estos caracteres (o textos, p. ej. //), se omite por completo y\n"
            "se pasa a la siguiente. Las líneas omitidas se ven tachadas.")
        self.entry_ignore.connect("changed", self._on_ignore_changed)
        area.pack_start(self.entry_ignore, False, False, 0)

        self.chk_keep = Gtk.CheckButton(label="Mantener la selección tras insertar")
        self.chk_keep.connect("toggled", self._on_keep_toggled)
        area.pack_start(self.chk_keep, False, False, 0)

        self.chk_apply = Gtk.CheckButton(label="Aplicar el estilo al hacer clic en su recuadro")
        self.chk_apply.set_tooltip_text(
            "Activado: pulsar un estilo lo elige y lo aplica a la capa de texto seleccionada.\n"
            "Desactivado: solo lo elige para las próximas inserciones; cada recuadro muestra\n"
            "un ✔ para aplicarlo a mano.\n"
            "Ojo: tras insertar, la capa recién creada suele quedar seleccionada; con esta opción\n"
            "activa, cambiar de estilo para la siguiente línea también la restilizaría.")
        self.chk_apply.connect("toggled", self._on_apply_click_toggled)
        area.pack_start(self.chk_apply, False, False, 0)

        irow = Gtk.Box(orientation=H, spacing=6)
        irow.pack_start(Gtk.Label(label="Imagen destino:", xalign=0), False, False, 0)
        self.image_combo = Gtk.ComboBoxText()
        self.image_combo.connect("changed", self._on_image_changed)
        irow.pack_start(self.image_combo, True, True, 0)
        area.pack_start(irow, False, False, 0)

        tip = Gtk.Label(xalign=0, wrap=True, max_width_chars=44)
        tip.set_markup("<small>Atajos de teclado: en GIMP, Editar ▸ Atajos de teclado, busca «TypeR» "
                       "(insertar siguiente, saltar, anterior, aplicar estilo).</small>")
        tip.get_style_context().add_class("dim-label")
        area.pack_start(tip, False, False, 0)
        self.settings_dlg = dlg

    def _open_dialog(self, dlg: Gtk.Dialog):
        dlg.show_all()
        dlg.present()

    # ------------------------------------------------------------------ #
    #  Estado ⇄ widgets
    # ------------------------------------------------------------------ #
    @contextmanager
    def _quiet(self):
        """Silencia los handlers mientras volcamos el estado a los widgets."""
        self._loading += 1
        try:
            yield
        finally:
            self._loading -= 1

    def _mutate(self, fn, resync: bool = False):
        """Lectura-modificación-escritura atómica y refresco de la vista."""
        with self.store.transaction() as st:
            fn(st)
        self.state = st
        if resync:
            self._sync_widgets()
        else:
            self._update_cards()
            self._refresh_script_view()

    def _reload(self):
        self.state = self.store.load()
        self._sync_widgets()

    def _sync_widgets(self):
        st = self.state
        with self._quiet():
            if not self._save_timer and self.buffer.get_text(
                    *self.buffer.get_bounds(), True) != st.script_text:
                self.buffer.set_text(st.script_text)
            if self.entry_ignore.get_text() != st.ignore_spec:
                self.entry_ignore.set_text(st.ignore_spec)
            self.chk_keep.set_active(st.keep_selection)
            self.chk_apply.set_active(st.apply_on_click)
            self._sync_cards()
            self._load_style_widgets()
        self._refresh_script_view()

    # ---- rejilla de estilos (píldoras) ------------------------------------- #
    def _sync_cards(self):
        names = list(self.state.styles)
        if names != self._card_names:
            self._rebuild_cards(names)
        self._update_cards()

    @staticmethod
    def _mini_button(names, fallback: str, tip: str, callback, name: str) -> Gtk.Button:
        btn = _icon_button(names, fallback, tip, "typer-mini")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_can_focus(False)
        btn.connect("clicked", callback, name)
        return btn

    def _rebuild_cards(self, names):
        for child in self.cards.get_children():
            self.cards.remove(child)
        self._cards = {}
        first = None
        for name in names:
            radio = Gtk.RadioButton() if first is None else Gtk.RadioButton.new_from_widget(first)
            first = first or radio
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=14)
            radio.add(label)                       # indicador ○ a la izquierda + nombre
            radio.connect("clicked", self._on_pill_clicked, name)

            pill = Gtk.Box(orientation=H, spacing=0)
            pill.get_style_context().add_class("style-pill")
            pill.pack_start(radio, True, True, 0)
            apply_btn = self._mini_button(
                ["object-select-symbolic", "emblem-ok-symbolic", "dialog-ok-symbolic",
                 "dialog-ok-apply-symbolic", "emblem-default-symbolic"], "✓",
                "Aplicar este estilo a la capa de texto seleccionada",
                self._on_pill_apply, name)
            apply_btn.set_no_show_all(True)        # su visibilidad la decide _update_cards
            pill.pack_start(apply_btn, False, False, 0)
            pill.pack_start(self._mini_button(
                ["document-edit-symbolic", "gtk-edit"], "✎", "Editar este estilo",
                self._on_pill_edit, name), False, False, 0)
            self.cards.add(pill)
            self._cards[name] = (pill, label, radio, apply_btn)
        self._card_names = names
        self.cards.show_all()

    def _update_cards(self):
        with self._quiet():
            active = self.state.active_style
            for name, (pill, label, radio, apply_btn) in self._cards.items():
                style = self.state.styles.get(name)
                if style is None:
                    continue
                label.set_markup(_name_markup(style))
                apply_btn.set_visible(not self.state.apply_on_click)   # con «aplicar al clic» sobra el ✔
                pill.set_tooltip_text(_style_summary(style))                      # detalle en tooltip
                is_active = name == active
                if is_active and not radio.get_active():
                    radio.set_active(True)
                ctx = pill.get_style_context()
                if is_active:
                    ctx.add_class("active")
                else:
                    ctx.remove_class("active")

    def _activate_style(self, name: str):
        if name != self.state.active_style:
            self._mutate(lambda st: setattr(st, "active_style", name), resync=True)

    def _on_pill_clicked(self, _radio, name):
        """Clic en el recuadro: elige el estilo (y, si está activado en Configuración,
        también lo aplica a la capa de texto seleccionada)."""
        if self._loading:
            return
        self._activate_style(name)
        if self.state.apply_on_click:
            self._run_action(actions.apply_style_to_selected,
                             soft_message=f"Estilo «{name}» seleccionado.")

    def _on_pill_apply(self, _btn, name):
        self._activate_style(name)
        self._run_action(actions.apply_style_to_selected)

    def _on_pill_edit(self, _btn, name):
        self._activate_style(name)
        self._open_dialog(self.editor_dlg)

    def _load_style_widgets(self):
        s = self.state.style()
        self.editor_dlg.set_title(f"Editar estilo · {s.name}")
        with self._quiet():
            self.w_name.set_text(s.name)
            self.w_font.set_font(f"{s.font} 12")
            self.w_size.set_value(s.size)
            self.w_lead.set_value(s.line_spacing_pct)
            self.w_bold.set_active(s.bold)
            self.w_italic.set_active(s.italic)
            self.w_upper.set_active(s.uppercase)
            self.w_fill.set_rgba(_rgba(s.fill_color))
            self.w_stroke_on.set_active(s.stroke_enabled)
            self.w_stroke_color.set_rgba(_rgba(s.stroke_color))
            self.w_stroke_w.set_value(s.stroke_width)
            self.w_fit.set_value(s.fit_ratio * 100)

    # ---- vista del guion --------------------------------------------------- #
    def _refresh_script_view(self):
        st, buf = self.state, self.buffer
        start, end = buf.get_bounds()
        for tag in (self.tag_ignored, self.tag_done, self.tag_active):
            buf.remove_tag(tag, start, end)

        n_lines = buf.get_line_count()
        for i in st.ignored_indices():                      # líneas omitidas → tachadas
            if i < n_lines:
                a = _iter_at_line(buf, i)
                b = a.copy()
                b.forward_to_line_end()
                buf.apply_tag(self.tag_ignored, a, b)

        entry = st.current_entry()
        done, total = st.progress()
        if entry is not None:
            a = _iter_at_line(buf, entry.index)
            if entry.index > 0:
                buf.apply_tag(self.tag_done, buf.get_start_iter(), a)
            b = a.copy()
            b.forward_to_line_end()
            buf.apply_tag(self.tag_active, a, b)
            self.textview.scroll_to_iter(a, 0.15, False, 0, 0)
            self.counter.set_text(f"{done + 1} / {total}")
            self.preview.set_markup(f"▶ <i>{html.escape(entry.text)}</i>")
            self.preview.set_tooltip_text(entry.text)             # el texto completo, al pasar el ratón
        else:
            if total:
                buf.apply_tag(self.tag_done, start, end)
            self.counter.set_text(f"{total} / {total}")
            self.preview.set_tooltip_text(None)
            self.preview.set_markup(
                "<i>— sin líneas pendientes —</i>" if total
                else "<i>Guion vacío: pulsa ✎ y pega el texto.</i>")

    def _status(self, msg: str, ok: bool = True):
        """Mensaje bajo el botón azul. Los éxitos se ocultan solos; los errores se quedan."""
        if self._status_timer:
            GLib.source_remove(self._status_timer)
            self._status_timer = 0
        if not msg:
            self.status.hide()
            return
        color = "#2e7d32" if ok else "#c62828"
        self.status.set_markup(f'<span foreground="{color}">{html.escape(msg)}</span>')
        self.status.show()
        if ok:
            self._status_timer = GLib.timeout_add_seconds(5, self._hide_status)

    def _hide_status(self):
        self._status_timer = 0
        self.status.hide()
        return False

    # ------------------------------------------------------------------ #
    #  Imágenes abiertas
    # ------------------------------------------------------------------ #
    def _refresh_images(self):
        current = None
        idx = self.image_combo.get_active()
        if 0 <= idx < len(self._images) and self._images[idx].is_valid():
            current = self._images[idx].get_id()
        elif self._image is not None and self._image.is_valid():
            current = self._image.get_id()
        images = list(Gimp.get_images())
        ids = [i.get_id() for i in images]
        new_ids = [i for i in ids if i not in self._known_image_ids]
        if self._known_image_ids and new_ids:       # se abrió otra página → pasa a ella
            current = new_ids[-1]
        self._known_image_ids = set(ids)
        with self._quiet():
            self._images = images
            self.image_combo.remove_all()
            for img in images:
                self.image_combo.append_text(img.get_name() or f"#{img.get_id()}")
            self.image_combo.set_active(ids.index(current) if current in ids else (0 if ids else -1))
        self._update_title()

    def _target_image(self):
        idx = self.image_combo.get_active()
        if 0 <= idx < len(self._images) and self._images[idx].is_valid():
            return self._images[idx]
        imgs = Gimp.get_images()
        return imgs[0] if imgs else None

    def _update_title(self):
        """La imagen destino va en el título: así se ve a qué página se insertará."""
        img = self._target_image()
        name = (img.get_name() if img is not None else None) or "sin imagen"
        self.set_title(f"TypeR · {name}")

    def _on_image_changed(self, *_):
        if not self._loading:
            self._update_title()

    # ------------------------------------------------------------------ #
    #  Guion: edición, clic, teclado y navegación
    # ------------------------------------------------------------------ #
    def _set_edit_mode(self, flag: bool):
        self._edit_mode = flag
        self.textview.set_editable(flag)
        self.textview.set_cursor_visible(flag)
        if self.edit_btn.get_active() != flag:
            self.edit_btn.set_active(flag)
        if flag:
            self.textview.grab_focus()
        else:
            self._flush_pending_script()

    def _on_edit_toggled(self, btn):
        self._set_edit_mode(btn.get_active())

    def _on_script_edited(self, *_):
        if self._loading:
            return
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(400, self._flush_script)

    def _flush_script(self):
        self._save_timer = 0
        text = self.buffer.get_text(*self.buffer.get_bounds(), True)
        self._mutate(lambda st: setattr(st, "script_text", text))
        return False

    def _flush_pending_script(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._flush_script()

    def _on_textview_press(self, view, event):
        """Modo navegación: un clic activa la línea pulsada."""
        if self._edit_mode or event.button != 1:
            return False
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        res = view.get_iter_at_location(bx, by)
        it = res[-1] if isinstance(res, tuple) else res
        view.grab_focus()                       # para que ↑/↓ funcionen enseguida
        self._select_line(it.get_line())
        return True                             # sin selección de texto ni caret

    def _on_textview_key(self, _view, event):
        if self._edit_mode:
            return False
        key = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and key in (Gdk.KEY_v, Gdk.KEY_V):          # Ctrl+V → modo edición y pega
            self._set_edit_mode(True)
            self.buffer.paste_clipboard(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD), None, True)
            return True
        if ctrl:
            return False                                    # Ctrl+C, etc. siguen funcionando
        if key == Gdk.KEY_Up:
            self._move(-1)
        elif key == Gdk.KEY_Down:
            self._move(+1)
        elif key == Gdk.KEY_Home:
            self._select_line(0)
        elif key == Gdk.KEY_End:
            self._select_line(10 ** 9)
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._run_action(actions.insert_next)
        else:
            return False
        return True

    def _select_line(self, line: int):
        self._flush_pending_script()
        was_ignored = line in self.state.ignored_indices()
        out = {}
        self._mutate(lambda st: out.update(entry=st.select_line(line)))
        entry = out.get("entry")
        if entry is None:
            self._status("El guion no tiene líneas válidas.", False)
        elif was_ignored:
            self._status(f"Esa línea está ignorada por el filtro; se activó la siguiente "
                         f"válida (línea {entry.index + 1}).", False)

    def _move(self, delta: int):
        self._flush_pending_script()
        self._mutate(lambda st: st.move(delta))

    def _on_skip(self, *_):
        self._flush_pending_script()
        self._mutate(lambda st: st.advance())

    # ---- opciones globales (ventana Configuración) -------------------------- #
    def _on_ignore_changed(self, entry):
        if self._loading:
            return
        spec = entry.get_text()
        self._mutate(lambda st: setattr(st, "ignore_spec", spec))     # re-tacha al instante

    def _on_keep_toggled(self, btn):
        if self._loading:
            return
        keep = btn.get_active()
        self._mutate(lambda st: setattr(st, "keep_selection", keep))

    def _on_apply_click_toggled(self, btn):
        if self._loading:
            return
        flag = btn.get_active()
        self._mutate(lambda st: setattr(st, "apply_on_click", flag))

    # ------------------------------------------------------------------ #
    #  Estilos: editar / nuevo / renombrar / eliminar
    # ------------------------------------------------------------------ #
    def _on_style_edited(self, *_):
        if self._loading:
            return
        base = self.state.style()
        family = self.w_font.get_font_desc().get_family() or base.font
        new = replace(
            base, font=family, size=self.w_size.get_value(),
            line_spacing_pct=self.w_lead.get_value(),
            bold=self.w_bold.get_active(), italic=self.w_italic.get_active(),
            uppercase=self.w_upper.get_active(),
            fill_color=_hex(self.w_fill.get_rgba()),
            stroke_enabled=self.w_stroke_on.get_active(),
            stroke_color=_hex(self.w_stroke_color.get_rgba()),
            stroke_width=int(self.w_stroke_w.get_value()),
            fit_ratio=self.w_fit.get_value() / 100.0,
        )
        self._mutate(lambda st: st.styles.__setitem__(base.name, new))   # la píldora se redibuja

    def _commit_name(self, *_):
        """Renombra el estilo activo con el texto del campo «Nombre»."""
        if self._loading:
            return False
        old = self.state.active_style
        new = self.w_name.get_text().strip()
        if not new or new == old:
            with self._quiet():
                self.w_name.set_text(old)
            return False
        if new in self.state.styles:
            self._status(f"Ya existe un estilo llamado «{new}».", False)
            with self._quiet():
                self.w_name.set_text(old)
            return False
        self._mutate(lambda st: st.rename_style(old, new), resync=True)
        return False

    def _ask_name(self, title: str, default: str):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Aceptar", Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=default, activates_default=True)
        dlg.get_content_area().add(entry)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        ok = dlg.run() == Gtk.ResponseType.OK
        name = entry.get_text().strip()
        dlg.destroy()
        return name if ok and name else None

    def _on_new_style(self, *_):
        base = self.state.style()
        name = self._ask_name("Nuevo estilo", f"{base.name} copia")
        if not name:
            return

        def fn(st):
            unique = _unique(name, st.styles)
            st.styles[unique] = replace(base, name=unique)
            st.active_style = unique
        self._mutate(fn, resync=True)

    def _on_delete_style(self, *_):
        if len(self.state.styles) <= 1:
            self._status("Debe quedar al menos un estilo.", False)
            return
        doomed = self.state.active_style

        def fn(st):
            st.styles.pop(doomed, None)
            st.active_style = next(iter(st.styles))
        self._mutate(fn, resync=True)

    # ------------------------------------------------------------------ #
    #  Acciones sobre GIMP
    # ------------------------------------------------------------------ #
    def _run_action(self, fn, soft_message: str | None = None):
        """Ejecuta una acción. `soft_message`: si no hay capa de texto seleccionada,
        no es un error (p. ej. al elegir un estilo con «aplicar al clic» activo)."""
        image = self._target_image()
        if image is None:
            self._status("No hay ninguna imagen abierta.", False)
            return
        try:
            self._flush_pending_script()          # usa el guion tal como está en pantalla
            self._status(fn(image, self.store), True)
        except NoTextLayerSelected as exc:
            self._status(soft_message or str(exc), soft_message is not None)
        except TypesetError as exc:
            self._status(str(exc), False)
        except Exception as exc:                  # no debe tumbar el panel
            traceback.print_exc()
            self._status(f"Error inesperado: {exc}", False)
        finally:
            self._reload()

    # ------------------------------------------------------------------ #
    #  Sincronización con atajos (otros procesos)
    # ------------------------------------------------------------------ #
    def _on_file_changed(self, _monitor, file, _other, _event):
        if file is None or file.get_basename() != StateStore.STATE_NAME or self._reload_source:
            return
        self._reload_source = GLib.idle_add(self._reload_idle)

    def _reload_idle(self):
        self._reload_source = 0
        self._reload()
        return False

    def _on_delete(self, *_):
        self._flush_pending_script()
        return False


def run_panel(image) -> None:
    GimpUi.init("typer-gimp")
    win = TypeRPanel(StateStore(), image)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    win.show_all()
    Gtk.main()
