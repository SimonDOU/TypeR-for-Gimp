# TypeR para GIMP

Una versión ligera y compacta basada en Python para traer las funciones más esenciales de *TypeR / typer tools* directamente a GIMP. 

> **Nota de compatibilidad**: Este plugin está pensado y optimizado para la versión nativa (instalación estándar) de **GIMP 3.2**. Las versiones Flatpak pueden presentar conflictos con dependencias del sistema y no están oficialmente soportadas por ahora.

---

## Características Principales

-  **Interfaz**: Cuadrícula de estilos distribuida en 2 columnas para aprovechar al máximo el espacio vertical en pantallas de trabajo.
-  **Paneles reajustables**: Integración de un divisor (`Gtk.Paned`) para ajustar dinámicamente el espacio entre el bloc de notas del guion y los controles del plugin.
-  **Aplicación al clic**: Capacidad de aplicar estilos de texto de forma directa e inmediata sobre la capa seleccionada.
-  **Filtros discretos en tiempo real**: Menú de configuración integrado para ignorar líneas vacías, marcas de dirección o patrones específicos del guion.
-  **Auto-validación interna**: Mecanismo de control de versión (`0.4.0`) que verifica la integridad de los submódulos y previene fallos por archivos desincronizados.
-  **Soporte de selección optimizado**: Adaptación nativa para la descompresión de tuplas de selección en la API de GIMP 3.2.

---

## Vista Previa

<img width="1920" height="1079" alt="imagen" src="https://github.com/user-attachments/assets/ffbd292c-ad80-4cad-bf05-e6262cc3fde1" />


---

## Estructura del Proyecto

El repositorio incluye el archivo clave **`typer-gimp.tar.gz`**, que contiene en su interior toda la estructura modular lista para desplegarse (`typer_core/`, submódulos y tests).

---

## Instalación Linux (arch/cachyos)

### Opción A: Directamente desde GitHub (Recomendado)

Abre tu terminal y ejecuta el siguiente bloque de comandos para clonar el repositorio, configurar los permisos y aplicar el parche de compatibilidad para la selección en GIMP 3.2:

```bash
# 1. Limpiar instalaciones previas
rm -rf ~/.config/GIMP/3.2/plug-ins/typer-gimp/

# 2. Clonar el repositorio en la carpeta de plugins de GIMP
mkdir -p ~/.config/GIMP/3.2/plug-ins/
git clone [https://github.com/SimonDOU/TypeR-for-Gimp.git](https://github.com/SimonDOU/TypeR-for-Gimp.git) ~/.config/GIMP/3.2/plug-ins/typer-gimp

# 3. Dar permisos de ejecución al script principal
chmod +x ~/.config/GIMP/3.2/plug-ins/typer-gimp/typer-gimp.py

# 4. Ajustar el parche de la API de selección para GIMP 3.2
python3 -c '
import re, os
path = os.path.expanduser("~/.config/GIMP/3.2/plug-ins/typer-gimp/typer_core/typesetter.py")
if os.path.exists(path):
    with open(path, "r") as f:
        text = f.read()
    text = re.sub(r".*Selection\.bounds.*", "    non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[-5:]", text)
    with open(path, "w") as f:
        f.write(text)
    print("¡TypeR instalado correctamente!")
```


### Opción B: Con el archivo ``typer-gimp.tar.gz``

```
# 1. Limpiar instalaciones previas
rm -rf ~/.config/GIMP/3.2/plug-ins/typer-gimp/

# 2. Descomprimir el archivo tar.gz directamente en la carpeta de plugins
mkdir -p ~/.config/GIMP/3.2/plug-ins/
tar -xzf typer-gimp.tar.gz -C ~/.config/GIMP/3.2/plug-ins/

# 3. Dar permisos de ejecución al script principal
chmod +x ~/.config/GIMP/3.2/plug-ins/typer-gimp/typer-gimp.py

# 4. Ajustar el parche de la API de selección para GIMP 3.2
python3 -c '
import re, os
path = os.path.expanduser("~/.config/GIMP/3.2/plug-ins/typer-gimp/typer_core/typesetter.py")
if os.path.exists(path):
    with open(path, "r") as f:
        text = f.read()
    text = re.sub(r".*Selection\.bounds.*", "    non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[-5:]", text)
    with open(path, "w") as f:
        f.write(text)
    print("¡TypeR instalado correctamente desde el .tar.gz!")
```


## Instalación Windows

### Opción A: con Terminal/poweshell

importante tener el archivo ``typer-gimp.tar.gz`` 
tener en consideración que este comando puede variar dependiendo de donde tengas instalado el tar y Gimp (en caso de dudas preguntale a gemini/chatGPT)

```
# 1. Definir la ruta de plugins de GIMP en Windows
$PluginDir = "$env:APPDATA\GIMP\3.2\plug-ins\typer-gimp"
$TarPath = "$env:USERPROFILE\Downloads\typer-gimp.tar.gz"

# 2. Limpiar versión anterior y extraer el tar.gz usando tar nativo de Windows 10/11
if (Test-Path $PluginDir) { Remove-Item -Recurse -Force $PluginDir }
New-Item -ItemType Directory -Force -Path $PluginDir | Out-Null
tar -xzf $TarPath -C "$env:APPDATA\GIMP\3.2\plug-ins\yper-gimp"

# 3. Aplicar el parche de compatibilidad para GIMP 3.2 en typesetter.py
$TypesetterPath = "$PluginDir\typer_core\typesetter.py"
if (Test-Path $TypesetterPath) {
    $content = Get-Content $TypesetterPath -Raw
    $newContent = [System.Text.RegularExpressions.Regex]::Replace($content, '.*Selection\.bounds.*', '    non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[-5:]')
    Set-Content -Path $TypesetterPath -Value $newContent
    Write-Host "¡TypeR instalado y parcheado exitosamente en Windows!" -ForegroundColor Green
} else {
    Write-Host "Error: No se encontró typesetter.py. Revisa la ruta del archivo comprimido." -ForegroundColor Red
}
```

### Opción B: Manualmente

1. Ubicar la carpeta de plugins en Windows

La ruta estándar para la versión nativa de GIMP en Windows suele ser:
``C:\Users\TU_USUARIO\AppData\Roaming\GIMP\3.2\plug-ins\``
(Nota: La carpeta AppData está oculta por defecto en el Explorador de archivos; puedes escribir ``%appdata%\GIMP\3.2\plug-ins`` directamente en la barra de direcciones).
2. Pasos de instalación manual

Descarga y extrae el contenido del archivo ``typer-gimp.tar.gz`` (puedes usar 7-Zip o WinRAR para descomprimirlo).

Entra a la carpeta descomprimida para asegurar que tienes la carpeta principal ``typer-gimp`` (que contiene ``typer-gimp.py`` y la ``subcarpeta typer_core``).

Copia la carpeta ``typer-gimp`` completa dentro de la ruta de plugins de GIMP mencionada arriba:
``C:\Users\TU_USUARIO\AppData\Roaming\GIMP\3.2\plug-ins\typer-gimp\``

3. Aplicar el parche de compatibilidad para GIMP 3.2 en Windows

Dado que la API de selección en GIMP 3.2 requiere un ajuste, debes abrir el archivo ``typesetter.py`` (ubicado dentro de ``typer-gimp\typer_core\``) con cualquier editor de texto (como Bloc de notas o VS Code) y buscar la línea que contiene ``Selection.bounds``.

Reemplaza esa línea por la siguiente:

```
non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[-5:]
```
lo guardas y listo.

una vez instalado, al abrir Gimp en la sección de "Filtros" debería aparecer al final la extensión

<img width="567" height="707" alt="imagen" src="https://github.com/user-attachments/assets/da43c44e-0a00-4db5-bc54-f9e226fed2d2" />






