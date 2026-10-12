#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TypeR para GIMP 3.x — punto de entrada.

Instalación (CachyOS / Arch, GIMP de pacman):
    ~/.config/GIMP/3.0/plug-ins/typer-gimp/typer-gimp.py   (chmod +x)
    ~/.config/GIMP/3.0/plug-ins/typer-gimp/typer_core/...
Flatpak: ~/.var/app/org.gimp.GIMP/config/GIMP/3.0/plug-ins/typer-gimp/

Procedimientos registrados (menú Filtros ▸ TypeR). Todos menos el panel son
«headless»: se pueden enlazar a atajos en Editar ▸ Atajos de teclado, lo que
en Wayland es la única vía fiable (una ventana GTK no puede capturar teclas
globales ni quedarse «siempre visible»).
"""
import os
import sys
import traceback

import gi

gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, GLib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PANEL = "typer-gimp-panel"
INSERT = "typer-gimp-insert"
APPLY = "typer-gimp-apply-style"
NEXT = "typer-gimp-next"
PREV = "typer-gimp-prev"

PROCEDURES = {
    PANEL: ("TypeR: Abrir panel…", "Abre el panel de guion, estilos y acciones."),
    INSERT: ("TypeR: Insertar siguiente línea",
             "Inserta la línea activa del guion centrada en la selección y avanza."),
    APPLY: ("TypeR: Aplicar estilo a la capa seleccionada",
            "Reaplica el estilo activo a las capas de texto seleccionadas."),
    NEXT: ("TypeR: Saltar línea", "Avanza el cursor del guion sin insertar."),
    PREV: ("TypeR: Línea anterior", "Retrocede el cursor del guion."),
}


class TypeRPlugin(Gimp.PlugIn):
    def do_query_procedures(self):
        return list(PROCEDURES)

    def do_create_procedure(self, name):
        label, blurb = PROCEDURES[name]
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self._make_runner(name), None)
        procedure.set_image_types("*")
        procedure.set_menu_label(label)
        procedure.add_menu_path("<Image>/Filters/TypeR")
        procedure.set_documentation(blurb, blurb, name)
        procedure.set_attribution("typer-gimp", "typer-gimp", "2026")
        return procedure

    @staticmethod
    def _make_runner(name):
        def runner(procedure, run_mode, image, *_rest):
            # Imports perezosos: el registro de procedimientos al arrancar GIMP
            # no debe cargar GTK ni nada pesado.
            try:
                import typer_core
                typer_core.verify_install()          # módulos de versiones mezcladas → error claro
                if name == PANEL:
                    from typer_core.panel import run_panel
                    typer_core.verify_install(("panel",))
                    run_panel(image)
                else:
                    from typer_core import actions
                    from typer_core.store import StateStore
                    store = StateStore()
                    if name == INSERT:
                        actions.insert_next(image, store)
                    elif name == APPLY:
                        actions.apply_style_to_selected(image, store)
                    elif name == NEXT:
                        actions.step(store, +1)
                    elif name == PREV:
                        actions.step(store, -1)
            except Exception as exc:
                if not getattr(type(exc), "expected", False):     # TypesetError = esperable
                    traceback.print_exc(file=sys.stderr)
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        return runner


Gimp.main(TypeRPlugin.__gtype__, sys.argv)
