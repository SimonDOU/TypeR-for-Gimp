"""typer_core — lógica y UI de TypeR para GIMP.

IMPORTANTE: los módulos de esta carpeta se actualizan JUNTOS. Si copias solo
uno (p. ej. panel.py) pueden faltar atributos o clases de los demás. Cada
módulo declara `__version__` y `verify_install()` comprueba que coincidan.
"""
__version__ = "0.4.0"

_DEFAULT_MODULES = ("models", "store", "typesetter", "actions")


def verify_install(modules=_DEFAULT_MODULES) -> None:
    """Lanza RuntimeError con un mensaje claro si hay módulos de versiones mezcladas."""
    import importlib

    problems = []
    for name in modules:
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except ImportError as exc:                    # p. ej. falta una clase nueva
            problems.append(f"{name}.py no es compatible: {exc}")
            continue
        found = getattr(mod, "__version__", None)
        if found != __version__:
            problems.append(f"{name}.py es de la versión {found or 'desconocida'} "
                            f"(se esperaba {__version__})")
    if problems:
        raise RuntimeError(
            "typer_core desincronizado: reemplaza TODOS los .py de la carpeta typer_core/ "
            "a la vez.\n- " + "\n- ".join(problems))
