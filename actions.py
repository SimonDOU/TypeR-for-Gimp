"""Acciones de alto nivel: unen el estado persistente con el Typesetter.

Las usan tanto los botones del panel como los procedimientos «headless»
(los que se enlazan a atajos de teclado en GIMP). Cada una devuelve el
mensaje de estado, o lanza TypesetError con un mensaje legible.
"""
from __future__ import annotations

__version__ = "0.4.0"   # debe coincidir con typer_core.__version__

from .store import StateStore
from .typesetter import Typesetter, TypesetError


def insert_next(image, store: StateStore) -> str:
    """Limpia la línea activa, la inserta centrada en la selección y avanza."""
    with store.transaction() as st:             # bloqueo: dos atajos rápidos no duplican línea
        entry = st.current_entry()
        if entry is None:
            if not st.script_text.strip():
                raise TypesetError("El bloc de notas está vacío. Abre el panel y pega el guion.")
            raise TypesetError("No quedan líneas por insertar (fin del guion).")
        style = st.style()
        warnings = Typesetter(image).insert_line(
            entry.text, style, label=f"L{entry.index + 1}", keep_selection=st.keep_selection
        )
        st.advance()                            # solo si la inserción tuvo éxito
        done, total = st.progress()
    msg = f"✔ Línea {entry.index + 1} insertada · «{style.name}» ({done}/{total})"
    return msg + ("  ⚠ " + " ".join(warnings) if warnings else "")


def apply_style_to_selected(image, store: StateStore) -> str:
    st = store.load()
    style = st.style()
    count = Typesetter(image).restyle_selected(style, keep_selection=st.keep_selection)
    return f"✔ Estilo «{style.name}» aplicado a {count} capa(s) de texto"


def step(store: StateStore, delta: int) -> str:
    """Mueve el cursor del guion sin insertar nada (+1 saltar, -1 retroceder)."""
    with store.transaction() as st:
        moved = st.advance() if delta > 0 else st.retreat()
        entry = st.current_entry()
    if not moved:
        raise TypesetError("No hay más líneas en esa dirección.")
    return f"Línea activa: {entry.index + 1}" if entry else "Fin del guion"
