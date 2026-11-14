# Diagnóstico y calibración PTZ

El script `calibrate_ptz.py` consulta capacidades ONVIF y ejecuta movimientos
controlados para comparar ejes separados con movimientos diagonales. No corrige
automáticamente el comportamiento de la cámara ni modifica `cameras.yaml`.

El proxy tiene `sequential_axes: true` en la C211: ejecuta primero tilt y luego
pan, cada uno a `speed` durante su duración calibrada, con un `Stop` entre ambos.
El resto de cámaras conserva el movimiento simultáneo por defecto. Se puede
desactivar con `sequential_axes: false`. Las trayectorias secuenciales tardan la
suma de las dos duraciones y no tienen la misma trayectoria que una diagonal.

Ejecuta los ejemplos desde este directorio con `python3`; el subcomando `fov`
necesita PyYAML para leer `cameras.yaml`. Para inspeccionar la cámara real usa
su dirección y puerto ONVIF; si usas
el puerto del proxy, las capacidades y `MoveStatus` pueden estar simulados.
Usa una URL alcanzable desde donde ejecutes el comando.

```bash
python3 calibrate_ptz.py --url http://c211.local:2020/onvif/service --user USUARIO probe
```

La contraseña se solicita sin mostrarla. Alternativamente, se pueden definir
`ONVIF_USER` y `ONVIF_PASSWORD` en el entorno, teniendo en cuenta que las variables
de entorno también pueden ser accesibles a otros procesos autorizados. No
incluyas credenciales en la URL o en argumentos de línea de comandos. Las
opciones comunes van **antes** del subcomando.

`probe` muestra `GetCapabilities`, `GetProfiles`, `GetServiceCapabilities`,
`GetNodes`, `GetConfigurationOptions` y `GetStatus`. Usa `--profile TOKEN`
antes de `probe` o del movimiento si el perfil no es `profile_1`. Si una
consulta PTZ falla se informará y continuarán las restantes. `--debug` muestra
las peticiones y respuestas SOAP con las credenciales WS-Security redactadas;
revisa siempre los logs antes de compartirlos.

## Comparar pan, tilt y diagonal

Coloca la cámara en una posición intermedia, observa el vídeo y detén otros
controladores, incluido el autotracking de Frigate, durante la prueba. Cada
orden pide confirmación y envía `Stop` incluso si se interrumpe con Ctrl+C o
falla la petición inicial. Si se pierde la conexión, no se puede garantizar que
la cámara reciba `Stop`: vigílala y prepárate para detenerla manualmente.
No se mueve nada durante `probe` o `status`.

```bash
# Tilt solamente; repite con signo positivo para probar la otra dirección.
python3 calibrate_ptz.py --url http://c211.local:2020/onvif/service --user USUARIO move --x 0 --y -0.8 --seconds 0.5

# Pan solamente.
python3 calibrate_ptz.py --url http://c211.local:2020/onvif/service --user USUARIO move --x -0.8 --y 0 --seconds 0.5

# Reproducir la velocidad diagonal observada en los logs.
python3 calibrate_ptz.py --url http://c211.local:2020/onvif/service --user USUARIO move --x -0.03 --y -0.8 --seconds 0.88
```

Para contrastar la inconsistencia de cabeceras observada en el proxy, añade
`--proxy-action` al subcomando `move` o `fov`: envía la acción SOAP
`RelativeMove` tanto con `ContinuousMove` como con `Stop`. Sin esa opción las
acciones corresponden a cada operación. Compara los resultados; una respuesta
HTTP 200 no garantiza movimiento físico. La posición y `MoveStatus` devueltos
por la cámara también pueden ser incorrectos.

## Calcular y comprobar la emulación FOV

Este comando carga `pan_seconds`, `tilt_seconds`, `speed` y `sequential_axes` de la cámara en
`cameras.yaml`, usa el mismo cálculo que el proxy y envía el `ContinuousMove`
resultante directamente a la URL indicada (uno por eje en modo secuencial):

```bash
python3 calibrate_ptz.py --url http://c211.local:2020/onvif/service --user USUARIO fov --camera c211 --x -0.03 --y -0.97
```

Añade `--diagonal` o `--sequential` al subcomando `fov` para comparar los dos
modos sin editar la configuración. Las pruebas `fov` realizan los comandos
directamente; no envían `RelativeMove` al proxy.

Para calibrar cada eje, envía un movimiento **de un solo eje** con `move` a la
velocidad `speed` configurada. Mide visualmente qué fracción del ancho o alto
del vídeo recorrió un punto fijo: por ejemplo, 0.5 si recorrió medio encuadre.
Para una duración de 0.5 segundos y un recorrido observado de 0.4 FOV:

```bash
python3 calibrate_ptz.py estimate --seconds 0.5 --fov 0.4
```

El resultado es una estimación de segundos por FOV para `pan_seconds` o
`tilt_seconds`, respectivamente, **a esa velocidad**. Repite en ambos sentidos
y distintas posiciones, manteniendo el mismo zoom; los motores, arranque,
límites y velocidades no lineales pueden hacer que la estimación varíe. Ajusta
manualmente `cameras.yaml`, reinicia el proxy y verifica de nuevo con `fov`.
La calibración de ejes aislados no demuestra que la cámara admita diagonales.

El script también se monta en el contenedor. Tras aplicar el cambio de Compose
con `docker compose up -d`, puede ejecutarse, por ejemplo, con
`docker compose exec -it onvif-fov-proxy python /app/calibrate_ptz.py ...`.
Para `fov` dentro del contenedor añade
`--config /app/config/cameras.yaml` después del subcomando.
El módulo compartido `fov_math.py` también se monta en el contenedor: tras
actualizar estos archivos ejecuta `docker compose up -d` para aplicar todos los
montajes y la configuración, sin necesidad de reconstruir la imagen.

## Calibración web

Para obtener las imágenes, configura en cada cámara una `snapshot_url` accesible
desde el contenedor, que devuelva JPEG, PNG o WebP. Por ejemplo, si Frigate está
accesible sin autenticación en ese puerto:

```yaml
cameras:
  c211:
    # Conserva el resto de parámetros de la cámara.
    snapshot_url: "http://frigate.local:5000/api/c211/latest.jpg"
```

Usa la dirección y el puerto reales de Frigate; si su API exige autenticación,
necesitarás un endpoint de snapshots accesible de forma segura desde el proxy.
Opcionalmente, `ptz_path` selecciona la ruta ONVIF de la cámara; por defecto es
`/onvif/service`. Aplica la configuración y los montajes con:

```bash
docker compose up -d
```

La interfaz web **no tiene autenticación propia**: cualquiera que alcance el
puerto puede obtener imágenes y, si conoce las credenciales ONVIF, controlar la
cámara. Restringe el acceso a una red de confianza o usa un proxy inverso con
autenticación; no la expongas a Internet. El tráfico HTTP no está cifrado: usa
HTTPS cuando sea necesario.

Abre `http://HOST_DEL_PROXY:8211/calibration` para c211 (`8210` para c210) e
introduce el usuario y contraseña **ONVIF de la cámara**. La contraseña no se
escribe en el archivo de configuración ni se guarda entre recargas.

Pulsa **Capturar imagen actual**. Al hacer clic en ella se solicita un
desplazamiento FOV entre -1 y 1 por eje, con el signo vertical ONVIF
invertido respecto a las coordenadas del navegador. Las flechas permiten pruebas
de un solo eje. Cada prueba obtiene una captura inmediatamente antes y otra
después de ejecutar la emulación configurada y las muestra lado a lado. Las
imágenes dependen de la actualización de Frigate y pueden presentar latencia.
Una cruz marca el centro en la imagen actual y en ambas capturas de comparación.
Ante un error de comunicación `Stop` podría no llegar a la cámara: vigila el
dispositivo y detén manualmente el movimiento si fuera necesario.

**Consultar posición** muestra los valores reales de pan y tilt devueltos por
`GetStatus`. El formulario acepta coordenadas absolutas entre -1 y 1 y envía
`AbsoluteMove` a la cámara; requiere que el perfil de la cámara soporte esa
operación. La aceptación de la orden no garantiza que haya terminado el
movimiento: vuelve a consultar la posición cuando se detenga. Estos controles
no convierten ni calibran automáticamente la geometría del montaje.

### Calibración óptica mediante un punto fijo

La calibración óptica requiere SciPy, añadido a la imagen. Tras esta
actualización hay que reconstruir **una vez** y recrear el contenedor:

```bash
docker compose up -d --build
```

En **Calibración óptica**, elige un punto fijo lejano que aparezca en todas las
capturas. Mueve la cámara manualmente, espera a que se detenga y pulsa
**Preparar muestra**. Comprueba visualmente que Frigate muestra el fotograma
correspondiente a esa posición y pulsa el punto en la imagen congelada. Repite
en al menos ocho posiciones diferentes, variando ambos ejes y procurando que el
punto cubra distintas zonas de la imagen. No cambies el zoom ni actives el
autotracking. La aplicación consulta `GetStatus` antes y después de la captura y
al marcar el punto, pero no puede detectar el retraso del vídeo ni movimientos
que ocurran fuera de esas consultas.

El cálculo estima el FOV horizontal y vertical, grados por unidad ONVIF de
cada eje, inclinación del eje en la primera muestra y error angular RMS.
Por la simetría del modelo, la inclinación de referencia se expresa en
[-90°, 90°] y el signo de pan corresponde a esa convención; no identifica por
sí solo una dirección física absoluta en la habitación.
Supone lente rectilínea, píxeles cuadrados, centro óptico en el centro de la
imagen, ejes perpendiculares, coordenadas ONVIF lineales en ángulo y un punto
suficientemente lejano para despreciar paralaje. Las especificaciones, la
distorsión de lente, el montaje real o un flujo recortado pueden invalidar estas
hipótesis: comprueba el error y valida las estimaciones en otras posiciones.
Se rechazan muestras insuficientes o ajustes ambiguos; un error bajo no
garantiza una calibración física correcta.

Las muestras y el resultado se mantienen mientras la página esté abierta.
**Descargar muestras y resultado** permite conservarlos como JSON sin credenciales
ni imágenes. Si el cálculo falla, `result` será `null` y `error` contendrá el
motivo; si aún no se ha calculado, ambos serán `null`. El error también se
muestra junto al botón de cálculo. Con **Importar muestras guardadas** puedes
recuperar las muestras de ese JSON; después debes calcular de nuevo el resultado.
La estimación no modifica `cameras.yaml` ni altera los movimientos del proxy.
