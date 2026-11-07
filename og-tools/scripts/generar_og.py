#!/usr/bin/env python3
"""Miniaturas Open Graph (WhatsApp / redes) para las invitaciones: una imagen propia por invitación.

Por cada inv-<nombre>-<slug>.html:
  1. DATOS: toma los valores que trae el propio HTML (objeto S) y los sobrescribe con los de Supabase
     (config de ohana_events), igual que hace la invitación al abrirse ({...S, ...config}).
  2. IMAGEN: abre la invitación real en un navegador (con esos datos), captura su pantalla de bienvenida
     y la compone en una miniatura 1200x630 con el nombre, la edad, la fecha y los colores/tipografía de
     SU temática (la paleta sale del sobre; la tipografía del nombre, de la fuente embebida del HTML).
  3. PESO: JPEG progresivo, <= 200 KB (WhatsApp ignora imágenes de más de ~300 KB).
  4. ETIQUETAS: reescribe al INICIO del <head> las metas og:title, og:description, og:image (URL absoluta,
     exclusiva de su slug, con ?v=<huella> para que WhatsApp la vuelva a pedir si algo cambia) y twitter:*.
  5. MANIFIESTO og/manifest.json: huella de los datos por slug. Si nada cambió, no toca nada.

Uso:
  python3 generar_og.py carpeta/ --datos datos.json              # datos exportados con assets/og_datos.sql
  python3 generar_og.py carpeta/ --supabase-url https://REF.supabase.co --key <anon>   # lee Supabase directo
  python3 generar_og.py inv-x-y.html                              # solo con los valores del HTML
Opciones: --salida-dir DIR (por defecto, EN SITIO)  --imagen-base URL  --url-sitio https://tu.sitio/
          --forzar  --max-kb 200  --solo slug1,slug2

MODO AUTOMÁTICO (GitHub Pages + GitHub Actions, sin claves secretas):
  python3 generar_og.py . --auto --github --comprobar --github-output "$GITHUB_OUTPUT"   # ¿qué cambió? (sin navegador)
  python3 generar_og.py . --auto --github                                                # regenera solo lo que cambió
  --auto    lee Supabase con la URL y la clave anon que ya trae cada HTML; si la lectura falla NO toca nada (sale con 2)
  --github  deduce la URL de Pages (usuario.github.io/repo, CNAME o docs/) y deja og/<slug>.jpg junto a cada .html
Se programa con assets/github-actions/og-miniaturas.yml (ver references/og-miniaturas.md).

Requiere (una vez):  pip install playwright pillow && playwright install chromium
Después hay que SUBIR og/<slug>.jpg al almacenamiento (subir_og.py) y publicar los .html actualizados.
"""
import asyncio, base64, datetime, glob, hashlib, html as htmllib, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, argparse
import urllib.request, urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from agregar_efectos_festejo import extraer_paleta, norm, lum, mezcla, sin_datos   # misma lectura de paleta

DISENO = 3                      # sube si cambias la plantilla: fuerza a regenerar todo
ANCHO, ALTO = 1200, 630
DIR_FUENTES = os.path.join(AQUI, '..', 'assets', 'fuentes')
FUENTE_RESPALDO = os.path.join(DIR_FUENTES, 'lilita-one-latin-400.woff2')
BUCKET_RUTA = '/storage/v1/object/public/ohana-media/og'
MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


# ---------------------------------------------------------------- lectura del HTML
def campo(s, nombre):
    m = re.search(r'\b' + nombre + r':(?:`([^`]*)`|(\d+))', s)
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else None

def leer_html(path):
    s = open(path, encoding='utf-8').read()
    d = {'ruta': path, 'html': s}
    ini = s.find('var S={childName')
    zona = s[ini:ini + 4000] if ini >= 0 else s
    for k in ('childName', 'age', 'dateText', 'timeText', 'targetDate', 'introMessage'):
        d[k] = campo(zona, k)
    sl = re.search(r'(?<=return`)[a-z0-9_-]+(?=`\}\)\(\)\s*,)', s)
    d['slug'] = sl.group(0) if sl else None
    if not d['slug']:
        m = re.fullmatch(r'inv-[a-z0-9]+-(.+)\.html', os.path.basename(path))
        d['slug'] = m.group(1) if m else None
    t = re.search(r'<title>(.*?)</title>', s, re.S)
    titulo = htmllib.unescape(t.group(1).strip()) if t else ''
    d['tema'] = titulo.split(' - ')[0].strip() or 'Invitación de cumpleaños'
    u = re.search(r'https://([a-z0-9]{20})\.supabase\.co', s)
    d['supabase'] = 'https://%s.supabase.co' % u.group(1) if u else None
    d['paleta'] = extraer_paleta(s)
    i = s.find('=(()=>{try{let u=new URL(location.href)')
    seg = s[max(0, i - 360):i] if i > 0 else ''
    mm = re.search(r'D=`([^`]*)`.*?O=`([^`]*)`', seg, re.S)
    d['url_modulo'], d['clave_modulo'] = (mm.group(1), mm.group(2)) if mm else (None, None)
    return d


def fecha_bonita(iso):
    try:
        f = datetime.datetime.fromisoformat(iso)
        return '%s %d de %s, %d' % (DIAS[f.weekday()], f.day, MESES[f.month - 1], f.year)
    except Exception:
        return None

def hora_bonita(iso):
    try:
        f = datetime.datetime.fromisoformat(iso)
        h = f.hour % 12 or 12
        return '%d:%02d %s' % (h, f.minute, 'PM' if f.hour >= 12 else 'AM')
    except Exception:
        return None


# ---------------------------------------------------------------- datos de Supabase
def pedir_json(url, key):
    cab = {'apikey': key}
    if not key.startswith('sb_'):
        cab['Authorization'] = 'Bearer ' + key        # las claves nuevas (sb_publishable_…) no son JWT: solo apikey
    rq = urllib.request.Request(url, headers=cab)
    with urllib.request.urlopen(rq, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))

def cargar_datos(a, slugs):
    """-> {slug: {'config': {...}, 'is_published': bool|None, 'expires_at': str|None}}"""
    filas = []
    if a.datos:
        crudo = json.load(open(a.datos, encoding='utf-8'))
        if isinstance(crudo, dict):
            filas = [{'slug': k, 'config': v} for k, v in crudo.items()]
        else:
            filas = crudo
            if filas and isinstance(filas[0], dict) and 'datos' in filas[0]:     # salida cruda del SQL (una celda)
                filas = filas[0]['datos']
    elif a.supabase_url and a.key:
        lista = ','.join(urllib.parse.quote(s) for s in slugs)
        filas = pedir_json('%s/rest/v1/ohana_events?select=slug,config,is_published,expires_at&slug=in.(%s)' % (a.supabase_url.rstrip('/'), lista), a.key)
    return {f['slug']: {'config': f.get('config') or {}, 'is_published': f.get('is_published'), 'expires_at': f.get('expires_at')} for f in filas if f.get('slug')}


def a_data_uri(url):
    """Descarga una imagen (si hay red) y la devuelve como data URI; None si falla."""
    try:
        rq = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(rq, timeout=8) as r:
            tipo = r.headers.get_content_type()
            if not tipo.startswith('image/'):
                return None
            return 'data:%s;base64,%s' % (tipo, base64.b64encode(r.read()).decode())
    except Exception:
        return None

def preparar_config(config, avisos):
    """Imágenes http(s) del config -> data URI (para que la captura no dependa de la red). Si no se pueden bajar, se
    quitan y la invitación usa la imagen incluida en el HTML."""
    c = dict(config or {})
    for k in list(c):
        v = c[k]
        if isinstance(v, str) and re.match(r'https?://', v) and re.search(r'(image|img|poster|avatar|photo)$', k, re.I):
            du = a_data_uri(v)
            if du:
                c[k] = du
            else:
                del c[k]
                avisos.append('no se pudo bajar %s (%s…): se usa la imagen incluida en el HTML' % (k, v[:50]))
    return c


# ---------------------------------------------------------------- color
def hsl(h):
    r, g, b = [v / 255 for v in (int(h[i:i + 2], 16) for i in (1, 3, 5))]
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    s = 0 if mx == mn else (mx - mn) / (1 - abs(2 * l - 1))
    return s, l

def oscurece(h, t):
    return '#%02x%02x%02x' % tuple(round(int(h[i:i + 2], 16) * (1 - t)) for i in (1, 3, 5))

def elegir_colores(paleta):
    base = [c for c in paleta if c.startswith('#')] or ['#e040a0', '#754ba3']
    vivos = sorted(base, key=lambda c: -(hsl(c)[0] * (1 - abs(hsl(c)[1] - .55))))
    acento = vivos[0]
    if hsl(acento)[1] < .45:
        acento = mezcla(acento, .12)
    oscuro = oscurece(sorted(base, key=lum)[0], .62)
    texto_acento = '#1b1b2f' if lum(acento) > .5 else '#ffffff'
    return {'acento': acento, 'oscuro': oscuro, 'oscuro2': oscurece(acento, .7), 'texto_acento': texto_acento}


# ---------------------------------------------------------------- tipografía
def fuente_embebida(s, familia):
    fam = (familia or '').split(',')[0].strip().strip('\'"')
    if not fam:
        return None, None
    reglas = [r for r in re.findall(r'@font-face\{[^}]*\}', s) if re.search(r'font-family:\s*[\'"]?' + re.escape(fam) + r'[\'"]?\s*[;}]', r)]
    return (' '.join(reglas[:2]), fam) if reglas else (None, None)

def slug_fuente(fam):
    return re.sub(r'[^a-z0-9]+', '-', fam.lower()).strip('-')

def fuente_local(familia, peso):
    """Busca <familia>-latin-<peso>.woff2 en assets/fuentes/. Si no está, intenta bajarla de Fontsource con npm
    (queda guardada para la próxima). Devuelve (css @font-face, familia) o (None, None)."""
    fam = (familia or '').split(',')[0].strip().strip('\'"')
    sl = slug_fuente(fam)
    if not sl or sl in ('inherit', 'sans-serif', 'serif', 'monospace', 'system-ui'):
        return None, None
    def candidatos():
        return {int(re.search(r'-latin-(\d+)\.woff2$', f).group(1)): f for f in glob.glob(os.path.join(DIR_FUENTES, sl + '-latin-*.woff2'))}
    c = candidatos()
    if not c and shutil.which('npm'):
        try:
            tmp = tempfile.mkdtemp()
            r = subprocess.run(['npm', 'pack', '@fontsource/' + sl, '--silent', '--pack-destination', tmp], capture_output=True, text=True, timeout=90)
            tgz = [x for x in os.listdir(tmp) if x.endswith('.tgz')]
            if r.returncode == 0 and tgz:
                with tarfile.open(os.path.join(tmp, tgz[0])) as t:
                    for m in t.getmembers():
                        mm = re.fullmatch(r'package/files/%s-latin-(\d+)-normal\.woff2' % re.escape(sl), m.name)
                        if mm:
                            open(os.path.join(DIR_FUENTES, '%s-latin-%s.woff2' % (sl, mm.group(1))), 'wb').write(t.extractfile(m).read())
                c = candidatos()
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            c = candidatos()
    if not c:
        return None, None
    try:
        p = int(peso)
    except Exception:
        p = 400
    w = min(c, key=lambda k: abs(k - p))
    b = base64.b64encode(open(c[w], 'rb').read()).decode()
    return "@font-face{font-family:'%s';font-weight:%d;src:url(data:font/woff2;base64,%s) format('woff2')}" % (fam, w, b), fam


def css_respaldo():
    b = base64.b64encode(open(FUENTE_RESPALDO, 'rb').read()).decode()
    return "@font-face{font-family:'OGRespaldo';src:url(data:font/woff2;base64,%s) format('woff2')}" % b


# ---------------------------------------------------------------- plantilla de la miniatura
ICO_CAL = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>'
ICO_SOBRE = '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3.5 7 8.5 6 8.5-6"/></svg>'

def plantilla_og(v):
    e = lambda t: htmllib.escape(t or '', quote=True)
    c = v['colores']
    fuentes = css_respaldo() + (' ' + v['font_css'] if v['font_css'] else '')
    fam_nombre = "'%s','OGRespaldo'" % v['font_fam'] if v['font_fam'] else "'OGRespaldo'"
    D, A, AT = c['oscuro'], c['acento'], c['texto_acento']
    contorno = ','.join('%dpx %dpx 0 %s' % (dx, dy, D) for dx, dy in ((-5, -5), (5, -5), (-5, 5), (5, 5), (0, -6), (0, 6), (-6, 0), (6, 0)))
    edad = ('¡Cumple %s años!' % v['edad']) if v['edad'] else '¡Estás invitado a mi fiesta!'
    if v['edad'] == '1':
        edad = '¡Cumple 1 añito!'
    return '''<!doctype html><html><head><meta charset="utf-8"><style>
%(fuentes)s
*{box-sizing:border-box;margin:0;padding:0}
body{width:1200px;height:630px;overflow:hidden;font-family:'OGRespaldo','Segoe UI',Arial,sans-serif}
.lienzo{position:relative;width:1200px;height:630px;overflow:hidden;background:%(D)s}
.fondo{position:absolute;left:-80px;top:-80px;width:1360px;height:790px;object-fit:cover;filter:blur(26px) saturate(1.35) brightness(.95)}
.velo{position:absolute;inset:0;background:linear-gradient(100deg,%(D)sf0 0%%,%(D)sc4 50%%,%(D)s6e 100%%)}
.puntos{position:absolute;inset:0;background-image:radial-gradient(circle,%(A)s55 0 7px,transparent 8px),radial-gradient(circle,#ffffff44 0 5px,transparent 6px),radial-gradient(circle,%(A)s44 0 10px,transparent 11px);background-size:260px 210px,190px 170px,340px 280px;background-position:20px 30px,90px 110px,150px 20px;opacity:.55}
.telefono{position:absolute;left:88px;top:36px;width:288px;height:558px;border-radius:40px;padding:11px;background:#0e1118;box-shadow:0 0 0 5px %(A)s,0 26px 54px rgba(0,0,0,.6);transform:rotate(-4deg)}
.telefono img{display:block;width:100%%;height:100%%;object-fit:cover;object-position:top center;border-radius:30px}
.texto{position:absolute;left:468px;width:690px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;gap:16px}
.tema{display:inline-block;white-space:nowrap;padding:8px 26px 10px;border-radius:999px;background:%(A)s;color:%(AT)s;font-size:34px;line-height:1.1;box-shadow:0 5px 0 %(D)s}
.nombre{display:inline-block;white-space:nowrap;font-family:%(fam)s;font-weight:%(peso)s;font-size:176px;line-height:1.02;color:#fff;text-transform:%(tt)s;text-shadow:%(contorno)s,0 12px 0 %(D)s}
.edad{display:inline-block;white-space:nowrap;font-size:62px;line-height:1.05;color:#fff;text-shadow:0 5px 0 rgba(0,0,0,.4)}
.fecha{display:inline-flex;align-items:center;gap:16px;white-space:nowrap;padding:12px 28px 14px;border-radius:22px;background:#ffffff;color:%(D)s;font-size:40px;line-height:1.1;box-shadow:0 6px 0 %(A)s}
.fecha svg{flex:0 0 auto;color:%(A)s}
.cta{display:inline-flex;align-items:center;gap:14px;white-space:nowrap;margin-top:4px;font-size:32px;line-height:1.1;color:#fff;opacity:.96}
.cta svg{color:%(A)s}
</style></head><body><div class="lienzo">
<img class="fondo" src="%(captura)s"><div class="velo"></div><div class="puntos"></div>
<div class="telefono"><img src="%(captura)s"></div>
<div class="texto">
 <div class="tema" id="tema">%(tema)s</div>
 <div class="nombre" id="nombre">%(nombre)s</div>
 <div class="edad" id="edad">%(edad)s</div>
 <div class="fecha" id="fecha">%(ico_cal)s<span id="ftxt">%(fecha)s</span></div>
 <div class="cta">%(ico_sobre)s<span>Toca para abrir tu invitación</span></div>
</div></div>
<script>
function ajusta(id,max,min,paso){var n=document.getElementById(id),f=parseFloat(getComputedStyle(n).fontSize);
 while(n.getBoundingClientRect().width>max&&f>min){f-=paso;n.style.fontSize=f+'px'}}
document.fonts.ready.then(function(){
 ajusta('nombre',690,84,4);ajusta('tema',690,22,1);ajusta('edad',690,36,2);ajusta('ftxt',560,24,1);
 document.body.setAttribute('data-listo','1')});
</script></body></html>''' % {
        'fuentes': fuentes, 'D': D, 'A': A, 'AT': AT, 'fam': fam_nombre, 'peso': v['font_peso'] or '400', 'tt': v['font_tt'] or 'none',
        'contorno': contorno, 'captura': v['captura_uri'], 'tema': e(v['tema']), 'nombre': e(v['nombre']), 'edad': e(edad),
        'fecha': e(v['fecha_linea']), 'ico_cal': ICO_CAL, 'ico_sobre': ICO_SOBRE}


# ---------------------------------------------------------------- navegador
async def capturar(browser, d, config, nombre, avisos):
    """Abre la invitación real con esos datos y devuelve (captura JPEG en data URI, info de la fuente del nombre)."""
    ctx = await browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, reduced_motion='reduce', locale='es-MX')
    pg = await ctx.new_page()
    errores = []
    pg.on('pageerror', lambda x: errores.append(str(x)[:120]))

    async def manejar(route):
        u = route.request.url
        if u.startswith(('file:', 'data:')):
            return await route.continue_()
        if 'supabase.co' in u or (d['url_modulo'] and u.startswith(d['url_modulo'])):
            h = {'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'content-type': 'application/json'}
            if route.request.method == 'OPTIONS':
                return await route.fulfill(status=204, headers=h)
            if '/rest/v1/ohana_events' in u:
                return await route.fulfill(status=200, headers=h, body=json.dumps([{'id': '00000000-0000-0000-0000-000000000001', 'config': config}]))
            return await route.fulfill(status=200, headers=h, body='[]')
        if 'fonts.googleapis.com' in u or 'fonts.gstatic.com' in u or route.request.resource_type == 'image':
            return await route.continue_()          # con internet cargan los íconos y las imágenes externas, como las ve el invitado
        return await route.abort()
    await pg.route('**/*', manejar)
    await pg.goto('file://%s?e=%s' % (os.path.abspath(d['ruta']), d['slug']))
    try:
        await pg.wait_for_selector('#intro-overlay', timeout=6000)
    except Exception:
        avisos.append('sin pantalla de bienvenida: la captura es de la pantalla de Inicio')
    await pg.wait_for_timeout(2200)
    await pg.evaluate('document.fonts.ready')
    await pg.evaluate('''()=>{for(const el of document.querySelectorAll('[class*="material-symbols"],[class*="material-icons"]')){
      const fs=parseFloat(getComputedStyle(el).fontSize)||16;
      if(el.getBoundingClientRect().width>fs*1.7){el.textContent='';el.style.display='inline-block';el.style.width='1em'}}}''')
    info = await pg.evaluate('''(nombre)=>{const norm=t=>(t||'').replace(/\\s+/g,'').toLowerCase();const n=norm(nombre);if(!n)return null;
      let mejor=null;
      for(const e of document.querySelectorAll('body *')){if(norm(e.textContent)!==n)continue;
        const r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)continue;
        if([...e.children].some(h=>norm(h.textContent)===n))continue;   // el más interno que contiene todo el nombre
        mejor=e;break}
      if(!mejor)return null;const c=getComputedStyle(mejor);return {fam:c.fontFamily,peso:c.fontWeight,tt:c.textTransform}}''', nombre)
    png = await pg.screenshot(type='png')
    await ctx.close()
    if errores:
        avisos.append('errores JS al abrir la invitación: ' + '; '.join(errores[:2]))
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert('RGB')
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=86)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode(), info


async def renderizar(browser, v):
    ctx = await browser.new_context(viewport={'width': ANCHO, 'height': ALTO}, device_scale_factor=1)
    pg = await ctx.new_page()
    await pg.set_content(plantilla_og(v), wait_until='load')
    await pg.wait_for_selector('body[data-listo="1"]', timeout=8000)
    png = await pg.screenshot(type='png', clip={'x': 0, 'y': 0, 'width': ANCHO, 'height': ALTO})
    await ctx.close()
    return png


def optimizar(png, max_kb):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert('RGB')
    for q in range(90, 44, -5):
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= max_kb * 1024:
            return buf.getvalue(), q
    return buf.getvalue(), q


# ---------------------------------------------------------------- etiquetas
def construir_tags(v):
    e = lambda t: htmllib.escape(t, quote=True)
    t, de, img, alt = e(v['og_title']), e(v['og_desc']), e(v['og_image']), e(v['og_alt'])
    l = ['<!--og-auto-->',
         '<meta name="description" content="%s" />' % de,
         '<meta property="og:type" content="website" />',
         '<meta property="og:locale" content="es_MX" />',
         '<meta property="og:title" content="%s" />' % t,
         '<meta property="og:description" content="%s" />' % de,
         '<meta property="og:image" content="%s" />' % img,
         '<meta property="og:image:secure_url" content="%s" />' % img,
         '<meta property="og:image:type" content="image/jpeg" />',
         '<meta property="og:image:width" content="%d" />' % ANCHO,
         '<meta property="og:image:height" content="%d" />' % ALTO,
         '<meta property="og:image:alt" content="%s" />' % alt]
    if v.get('og_url'):
        l.append('<meta property="og:url" content="%s" />' % e(v['og_url']))
    l += ['<meta name="twitter:card" content="summary_large_image" />',
          '<meta name="twitter:title" content="%s" />' % t,
          '<meta name="twitter:description" content="%s" />' % de,
          '<meta name="twitter:image" content="%s" />' % img,
          '<meta name="twitter:image:alt" content="%s" />' % alt,
          '<!--/og-auto-->']
    return '\n'.join(l) + '\n'


def aplicar_tags(s, tags):
    """Quita las metas de descripción/og/twitter del encabezado y pone el bloque nuevo justo tras el viewport."""
    s = re.sub(r'<!--og-auto-->.*?<!--/og-auto-->\n?', '', s, flags=re.S)
    fin = min([i for i in (s.find('<script'), s.find('<style'), s.find('<link')) if i >= 0] or [len(s)])
    zona = s[:fin]
    zona = re.sub(r'[ \t]*<meta\s+(?:name="description"|property="og:[^"]*"|name="twitter:[^"]*")[^>]*>\n?', '', zona)
    m = re.search(r'<meta\s+name="viewport"[^>]*>\n?', zona)
    if not m:
        raise ValueError('no encontré <meta name="viewport"> en el encabezado')
    pos = m.end()
    if not zona[:pos].endswith('\n'):
        tags = '\n' + tags
    return zona[:pos] + tags + zona[pos:] + s[fin:]


# ---------------------------------------------------------------- principal
IMG_CLAVE = ('image', 'img', 'poster', 'avatar', 'photo')


def descubrir(entrada, recursivo):
    if os.path.isfile(entrada):
        return [entrada]
    if not recursivo:
        return sorted(glob.glob(os.path.join(entrada, 'inv-*.html')))
    out = []
    for raiz, dirs, files in os.walk(entrada):
        dirs[:] = [x for x in dirs if x not in ('.git', 'node_modules', 'og', '.github')]
        out += [os.path.join(raiz, f) for f in files if re.fullmatch(r'inv-.+\.html', f)]
    return sorted(out)


def resolver_github(a, archivos):
    """Dentro de GitHub Actions: (url pública del sitio, carpeta raíz de Pages) a partir de GITHUB_REPOSITORY."""
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not repo or '/' not in repo:
        sys.exit('✗ --github solo funciona dentro de GitHub Actions (falta GITHUB_REPOSITORY)')
    dueno, nombre = repo.split('/', 1)
    base = a.entrada if os.path.isdir(a.entrada) else os.path.dirname(a.entrada)
    if a.raiz_pages:
        raiz = os.path.join(base, a.raiz_pages)
    else:                                   # si TODAS las invitaciones están en docs/, Pages publica desde /docs
        rel = [os.path.relpath(f, base).replace(os.sep, '/') for f in archivos]
        raiz = os.path.join(base, 'docs') if rel and all(r.startswith('docs/') for r in rel) else base
    cname = os.path.join(raiz, 'CNAME')
    if a.url_sitio:
        url = a.url_sitio.rstrip('/')
    elif os.path.exists(cname) and open(cname, encoding='utf-8').read().strip():
        url = 'https://' + open(cname, encoding='utf-8').read().split()[0]
    elif nombre.lower() == dueno.lower() + '.github.io':
        url = 'https://%s.github.io' % dueno.lower()
    else:
        url = 'https://%s.github.io/%s' % (dueno.lower(), nombre)
    return url, os.path.abspath(raiz)


def datos_auto(docs):
    """Modo automático: lee Supabase con la URL y la clave anon que ya trae cada HTML.
    -> {slug: ('ok', fila) | ('sin_fila',) | ('error', msg) | ('sin_conexion',)}"""
    estado, grupos = {}, {}
    for d in docs:
        if not d['url_modulo'] or not d['clave_modulo']:
            estado[d['slug']] = ('sin_conexion',)
        else:
            grupos.setdefault((d['url_modulo'], d['clave_modulo']), []).append(d)
    for (u, k), ds in grupos.items():
        slugs = [d['slug'] for d in ds]
        try:
            filas = pedir_json('%s/rest/v1/ohana_events?select=slug,config,is_published,expires_at&slug=in.(%s)'
                               % (u.rstrip('/'), ','.join(urllib.parse.quote(x) for x in slugs)), k)
            if not isinstance(filas, list):
                raise ValueError('respuesta inesperada de Supabase')
            por = {f['slug']: f for f in filas if f.get('slug')}
            for x in slugs:
                estado[x] = ('ok', por[x]) if x in por else ('sin_fila',)
        except Exception as e:
            for x in slugs:
                estado[x] = ('error', str(e)[:140])
    return estado


def planear(a, d, fila, ahora, base_img, avisos):
    """Todo lo que NO necesita navegador: textos, huella, URL de la imagen y etiquetas."""
    config = {k: v for k, v in (fila.get('config') or {}).items() if k != 'fotoPropia'}
    if (fila.get('config') or {}).get('fotoPropia'):
        avisos.append('tiene foto propia guardada en Supabase: solo se incluye si lees con --supabase-url/--key o --auto')
    if fila.get('is_published') is False:
        avisos.append('el evento NO está publicado en Supabase (la invitación mostrará "no disponible")')

    def val(k):
        x = config.get(k)
        return x if x not in (None, '') else d[k]
    nombre = (val('childName') or '').strip()
    edad = str(val('age') or '').strip()
    target = val('targetDate')
    tcfg = config.get('targetDate')   # fecha nueva en Supabase sin su texto -> el texto se calcula de ella (nunca la fecha vieja del HTML)
    fecha = config.get('dateText') or (fecha_bonita(tcfg) if tcfg else None) or d['dateText'] or fecha_bonita(target or '') or ''
    hora = config.get('timeText') or (hora_bonita(tcfg) if tcfg else None) or d['timeText'] or hora_bonita(target or '') or ''
    if not nombre:
        avisos.append('no hay nombre del festejado: se usa "Mi fiesta"'); nombre = 'Mi fiesta'
    try:
        if target and datetime.datetime.fromisoformat(target) < ahora:
            avisos.append('LA FECHA YA PASÓ (%s): la miniatura mostrará esa fecha' % target[:10])
    except Exception:
        pass
    fecha_linea = fecha + (' · ' + hora if hora else '')
    imgs = {k: hashlib.sha1(str(v).encode()).hexdigest()[:10] for k, v in sorted(config.items()) if k.lower().endswith(IMG_CLAVE)}
    huella = hashlib.sha1(json.dumps([DISENO, nombre, edad, fecha_linea, d['tema'], d['paleta'], imgs], ensure_ascii=False).encode()).hexdigest()[:8]
    url_img = '%s/%s.jpg?v=%s' % (base_img.rstrip('/'), d['slug'], huella)
    titulo = '%s — ¡%s cumple %s!' % (d['tema'], nombre, edad) if edad else '%s — ¡Fiesta de %s!' % (d['tema'], nombre)
    desc = ('📅 %s. Abre tu invitación, mira los detalles y confirma tu asistencia.' % fecha_linea) if fecha_linea else 'Abre tu invitación, mira los detalles y confirma tu asistencia.'
    v = {'og_title': titulo, 'og_desc': desc, 'og_image': url_img,
         'og_alt': 'Invitación digital de cumpleaños de %s%s — %s' % (nombre, ' (%s años)' % edad if edad else '', d['tema'])}
    if a.og_url and a.url_sitio:
        v['og_url'] = '%s/%s?e=%s' % (a.url_sitio.rstrip('/'), os.path.basename(d['ruta']), d['slug'])
    return {'config': config, 'nombre': nombre, 'edad': edad, 'fecha_linea': fecha_linea, 'huella': huella, 'url_img': url_img,
            'titulo': titulo, 'desc': desc, 'v': v}


async def ejecutar(a, archivos):
    todos = [leer_html(f) for f in archivos]
    docs = [d for d in todos if d['slug']]
    sin_slug = [d for d in todos if not d['slug']]
    if a.solo:
        docs = [d for d in docs if d['slug'] in a.solo.split(',')]
    ahora = datetime.datetime.now()
    url_sitio, raiz_pages = a.url_sitio, None
    if a.github:
        url_sitio, raiz_pages = resolver_github(a, archivos)
        a.url_sitio = url_sitio
    estado = datos_auto(docs) if a.auto else None
    manual = {} if a.auto else cargar_datos(a, [d['slug'] for d in docs])

    pw = browser = None
    cambios, fallos, resumen = [], 0, []
    for d in sin_slug:
        resumen.append((os.path.basename(d['ruta']), 'OMITIDA', 0, ['no encuentro el slug por defecto en este HTML (¿es de la familia Ohana?)'], '', '', ''))
    grupos = {}
    for d in docs:
        grupos.setdefault(os.path.dirname(os.path.abspath(d['ruta'])), []).append(d)

    for dir_in, ds in sorted(grupos.items()):
        out_dir = (a.salida_dir if (a.salida_dir and len(grupos) == 1) else dir_in)
        os.makedirs(out_dir, exist_ok=True)
        og_dir = os.path.join(out_dir, 'og'); man_path = os.path.join(og_dir, 'manifest.json')
        manifiesto = json.load(open(man_path, encoding='utf-8')) if os.path.exists(man_path) else {}
        if a.imagen_base:
            base_dir = a.imagen_base
        elif (a.imagenes_en_sitio or a.github) and url_sitio:
            rel = os.path.relpath(out_dir, raiz_pages or (a.entrada if os.path.isdir(a.entrada) else os.path.dirname(a.entrada))).replace(os.sep, '/')
            base_dir = '/'.join([url_sitio.rstrip('/')] + ([urllib.parse.quote(rel)] if rel not in ('.', '') else []) + ['og'])
        else:
            base_dir = None
        tocado = False
        for d in ds:
            slug, avisos = d['slug'], []
            if a.auto:
                est = estado[slug]
                if est[0] == 'error':
                    fallos += 1; resumen.append((slug, 'ERROR', 0, ['no se pudo leer Supabase: %s (no se toca nada)' % est[1]], '', '', '')); continue
                if est[0] == 'sin_conexion':
                    resumen.append((slug, 'OMITIDA', 0, ['el HTML no trae conexión a Supabase'], '', '', '')); continue
                if est[0] == 'sin_fila':
                    resumen.append((slug, 'OMITIDA', 0, ['sin fila pública (no publicada o caducada): no se toca nada'], '', '', '')); continue
                fila = est[1]
            else:
                fila = manual.get(slug)
                if fila is None:
                    avisos.append('sin fila en los datos de Supabase: se usan solo los valores del HTML'); fila = {'config': {}}
            base = base_dir or ((d['supabase'] + BUCKET_RUTA) if d['supabase'] else None)
            if not base:
                sys.exit('✗ %s: no hay URL de Supabase en el HTML; indica --imagen-base https://…/og' % os.path.basename(d['ruta']))
            P = planear(a, d, fila, ahora, base, avisos)
            jpg_path = os.path.join(og_dir, slug + '.jpg')
            previo = manifiesto.get(slug, {})
            html_dest = os.path.join(out_dir, os.path.basename(d['ruta']))
            igual = previo.get('huella') == P['huella'] and os.path.exists(jpg_path) and not a.forzar
            try:
                nuevo_html = aplicar_tags(d['html'], construir_tags(P['v']))
            except ValueError as e:
                fallos += 1; resumen.append((slug, 'ERROR', 0, ['no se pudo escribir las etiquetas: %s' % e], '', '', '')); continue
            actual = open(html_dest, encoding='utf-8').read() if os.path.exists(html_dest) else None
            cambio_html = nuevo_html != actual
            estado_txt = 'sin cambios'
            if not igual:
                if a.comprobar:
                    estado_txt = 'POR ACTUALIZAR'
                else:
                    if browser is None:
                        from playwright.async_api import async_playwright
                        pw = await async_playwright().start(); browser = await pw.chromium.launch()
                    config = preparar_config(P['config'], avisos)
                    captura, info = await capturar(browser, d, config, P['nombre'], avisos)
                    fcss, ffam = fuente_embebida(d['html'], info['fam']) if info else (None, None)
                    if info and not fcss:
                        fcss, ffam = fuente_local(info['fam'], info['peso'])
                        nom = info['fam'].split(',')[0].strip('\'" ')
                        if not fcss and slug_fuente(nom) != 'lilita-one':
                            avisos.append('la tipografía del nombre (%s) no está en el HTML ni en assets/fuentes/: se usa Lilita One' % nom)
                    png = await renderizar(browser, {**P['v'], 'colores': elegir_colores(d['paleta']), 'captura_uri': captura, 'tema': d['tema'],
                                                      'nombre': P['nombre'], 'edad': P['edad'], 'fecha_linea': P['fecha_linea'] or 'Fecha por confirmar',
                                                      'font_css': fcss, 'font_fam': ffam, 'font_peso': (info or {}).get('peso'), 'font_tt': (info or {}).get('tt')})
                    jpg, q = optimizar(png, a.max_kb)
                    os.makedirs(og_dir, exist_ok=True)
                    open(jpg_path, 'wb').write(jpg)
                    manifiesto[slug] = {'huella': P['huella'], 'kb': round(len(jpg) / 1024, 1), 'calidad': q, 'url': P['url_img'], 'nombre': P['nombre'],
                                        'edad': P['edad'], 'fecha': P['fecha_linea'], 'tema': d['tema'], 'generado': ahora.strftime('%Y-%m-%d %H:%M')}
                    estado_txt = 'NUEVA' if not previo else 'ACTUALIZADA'
                    tocado = True
            if a.comprobar:
                if (not igual) or cambio_html:
                    cambios.append(slug)
                    if igual:
                        estado_txt = 'POR ACTUALIZAR (solo etiquetas)'
            elif not igual or cambio_html:
                cambios.append(slug)
            if not a.comprobar and cambio_html:
                open(html_dest, 'w', encoding='utf-8').write(nuevo_html)
            kb = manifiesto.get(slug, {}).get('kb', 0)
            resumen.append((slug, estado_txt, kb, avisos, P['titulo'], P['desc'], P['url_img'],
                            'HTML actualizado' if (cambio_html and not a.comprobar) else ('HTML por actualizar' if cambio_html else 'HTML igual')))
        if tocado and not a.comprobar:
            json.dump(manifiesto, open(man_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if browser is not None:
        await browser.close(); await pw.stop()

    print('\nMiniaturas Open Graph  (%s)' % ('solo comprobación, no se cambió nada' if a.comprobar else 'listo'))
    for fila in resumen:
        slug, est, kb, avisos, titulo, desc, url = fila[:7]
        extra = fila[7] if len(fila) > 7 else ''
        print('\n● %-18s %-30s %6.1f KB   %s' % (slug, est, kb, extra))
        if titulo:
            print('    og:title       ', titulo); print('    og:description ', desc); print('    og:image       ', url)
        for x in avisos:
            print('    ⚠', x)
    print('\nResumen: %d con cambios, %d con error.' % (len(cambios), fallos))
    if a.github_output:
        with open(a.github_output, 'a', encoding='utf-8') as f:
            f.write('cambios=%s\nlista=%s\nfallos=%d\n' % ('si' if cambios else 'no', ','.join(cambios), fallos))
    return 2 if fallos else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('entrada', help='carpeta con inv-*.html o un archivo .html')
    ap.add_argument('--datos', help='JSON exportado con assets/og_datos.sql (o {slug: config})')
    ap.add_argument('--supabase-url'); ap.add_argument('--key', help='clave anon/publishable (nunca la service_role)')
    ap.add_argument('--auto', action='store_true', help='lee Supabase con la URL/clave que trae cada HTML; si falla la lectura NO toca nada')
    ap.add_argument('--comprobar', action='store_true', help='solo revisa qué cambió (sin navegador y sin escribir nada)')
    ap.add_argument('--recursivo', action='store_true', help='busca inv-*.html en subcarpetas (implícito con --github)')
    ap.add_argument('--github', action='store_true', help='dentro de GitHub Actions: deduce la URL de GitHub Pages y publica las imágenes junto a los .html')
    ap.add_argument('--raiz-pages', help='carpeta que publica Pages (p. ej. docs). Por defecto se deduce')
    ap.add_argument('--github-output', help='archivo $GITHUB_OUTPUT donde escribir cambios=si|no, lista=, fallos=')
    ap.add_argument('--salida-dir', help='dónde dejar los .html actualizados y og/ (por defecto, en la misma carpeta)')
    ap.add_argument('--imagen-base', help='URL pública donde se publicarán los JPG (por defecto: bucket ohana-media/og del proyecto del HTML)')
    ap.add_argument('--imagenes-en-sitio', action='store_true', help='los JPG van en og/ junto a cada .html y la URL se arma con --url-sitio')
    ap.add_argument('--url-sitio', help='URL pública del sitio (p. ej. https://usuario.github.io/repo)')
    ap.add_argument('--og-url', action='store_true', help='agrega también og:url (no recomendado si compartes enlaces con parámetros por invitado)')
    ap.add_argument('--solo', help='slugs separados por coma'); ap.add_argument('--max-kb', type=int, default=200)
    ap.add_argument('--forzar', action='store_true', help='regenera aunque nada haya cambiado')
    a = ap.parse_args()
    if a.key and a.key.count('.') == 2:
        try:
            carga = json.loads(base64.urlsafe_b64decode(a.key.split('.')[1] + '=='))
            if carga.get('role') == 'service_role':
                sys.exit('✗ Esa es la clave service_role (secreta). Usa la anon/publishable.')
        except Exception:
            pass
    archivos = descubrir(a.entrada, a.recursivo or a.github)
    if not archivos:
        sys.exit('✗ no hay archivos inv-*.html en ' + a.entrada)
    if a.salida_dir:
        os.makedirs(a.salida_dir, exist_ok=True)
    sys.exit(asyncio.run(ejecutar(a, archivos)))


if __name__ == '__main__':
    main()
