#!/usr/bin/env python3
"""Agrega a una invitación los dos efectos "de fiesta" de la referencia (Chicas Poderosas):

  1. El SOBRE/CARTA de la bienvenida se AGITA (sacudida corta cada 3 s) sin perder el brillo del tema.
  2. Cada CLIC/TOQUE hace DESTELLOS: chispas que salen disparadas; el primer toque suma un flash de luz
     y un anillo que se expande.

Los colores salen SOLOS de la propia invitación (solapas, sello y brillo del sobre), así cada temática
destella en sus colores. También se pueden fijar con --colores.

Qué NO hace a propósito: no agrega el arcoíris superior, los emojis que suben ni las burbujas de texto
(¡POW!) de la referencia; esas nunca fueron el "destello" y son propias de ese tema.

Se inserta antes de </body> como <style id="fx-festejo"> + <script id="fx-festejo-js"> (idempotente: si ya
existe, lo regenera). Respeta prefers-reduced-motion. Se apaga al abrir el sobre (.is-open).

Uso:
  python3 agregar_efectos_festejo.py in.html out.html
  python3 agregar_efectos_festejo.py in.html out.html --colores '#e52521,#0e6fd1,#ffd93b'
  python3 agregar_efectos_festejo.py in.html out.html --sin-sacudida      # solo destellos
  python3 agregar_efectos_festejo.py in.html out.html --sin-destellos     # solo sacudida
  python3 agregar_efectos_festejo.py in.html out.html --quitar            # quita el bloque
Se ejecuta DESPUÉS de tematizar.py (para que la paleta ya sea la final) y de agregar_reloj_bienvenida.py.
"""
import re, sys, os, argparse, subprocess, tempfile

HEX = re.compile(r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b')
ID_CSS, ID_JS = 'fx-festejo', 'fx-festejo-js'
GLOW_DEFECTO = 'ohanaGlow 2.6s ease-in-out infinite'


# ---------- color ----------
def norm(h):
    h = h.lower()
    if len(h) == 4:
        h = '#' + ''.join(c * 2 for c in h[1:])
    return h[:7]

def rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]

def lum(h):
    r, g, b = [v / 255 for v in rgb(h)]
    return .2126 * r + .7152 * g + .0722 * b

def mezcla(h, t):
    """Aclara h mezclándolo con blanco (t = 0..1)."""
    return '#%02x%02x%02x' % tuple(round(v + (255 - v) * t) for v in rgb(h))

def sin_datos(s):
    return re.sub(r'data:[a-zA-Z0-9/+.\-]+;base64,[A-Za-z0-9+/=]{100,}', '', s)

def ultimo(css, sel):
    ms = re.findall(re.escape(sel) + r'\{([^}]*)\}', css)
    return ms[-1] if ms else ''

def extraer_paleta(s):
    """Colores de la temática a partir del propio sobre (solapas, sello, texto, brillo)."""
    css = sin_datos(s)
    cols = []
    for sel in ('.ohana-env__flap--top', '.ohana-env__flap--left', '.ohana-env__flap--right',
                '.ohana-env__flap--bottom', '.ohana-env__seal', '.ohana-env__hint', '.ohana-env__title'):
        cols += [norm(h) for h in HEX.findall(ultimo(css, sel))]
    m = re.search(r'@keyframes (?:ohanaGlow|sfx-glow)\{(.*?)\}\}', css)
    if m:
        cols += [norm(h) for h in HEX.findall(m.group(1))]
    base = []
    for c in cols:
        if lum(c) < .18 or lum(c) > .93 or c in base:
            continue
        base.append(c)
    return base

def componer_paleta(base):
    if len(base) < 2:
        base = base + [c for c in ('#ffd93b', '#ff5fa2') if c not in base]
    base = base[:4]
    out = list(base)
    for c in base:
        t = mezcla(c, .45)
        if t not in out:
            out.append(t)
    out.append('#ffffff')
    return out[:9]


# ---------- brillo existente del sobre ----------
def sin_movimiento_reducido(css):
    """Quita los bloques @media (prefers-reduced-motion...){...} (llaves anidadas)."""
    while True:
        i = css.find('@media (prefers-reduced-motion')
        if i < 0:
            return css
        j = css.find('{', i)
        d, k = 1, j + 1
        while d and k < len(css):
            d += (css[k] == '{') - (css[k] == '}')
            k += 1
        css = css[:i] + css[k:]


def detectar_glow(s):
    """Animación de brillo que YA usa .ohana-env (la última declarada). None si no tiene."""
    css = sin_datos(s)
    css = re.sub(r'<style id="%s">.*?</style>' % ID_CSS, '', css, flags=re.S)
    css = sin_movimiento_reducido(css)
    decl = re.findall(r'(?<![\w\-.#:>])\.ohana-env\{([^}]*)\}', css)
    anim = None
    for d in decl:
        m = re.search(r'animation:([^;}]*)', d)
        if m:
            anim = m.group(1).replace('!important', '').strip()
    if not anim or anim == 'none':
        return None
    nombre = anim.split()[0]
    if not re.search(r'@keyframes\s+' + re.escape(nombre) + r'\b', css):
        return None
    return anim


# ---------- bloques ----------
def bloque_css(pal, glow, sacudida, destellos):
    c0, c1 = pal[0], (pal[1] if len(pal) > 1 else pal[0])
    r = []
    if destellos:
        r += [
            '#fx-festejo-capa{position:fixed;inset:0;z-index:60;pointer-events:none;overflow:hidden}',
            '.fx-spark{position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:var(--c);box-shadow:0 0 8px var(--c);animation:fxSpark .8s ease-out forwards}',
            '@keyframes fxSpark{to{transform:translate(var(--x),var(--y)) scale(.2);opacity:0}}',
            '.fx-ring{position:absolute;width:20px;height:20px;margin:-10px 0 0 -10px;border:6px solid #fff;border-radius:50%;box-shadow:0 0 0 4px @C0@,0 0 0 8px @C1@;animation:fxRing .9s ease-out forwards}',
            '@keyframes fxRing{to{transform:scale(var(--s,22));opacity:0}}',
            '.fx-flash{position:fixed;inset:0;background:radial-gradient(circle at var(--fx) var(--fy),#fff 0,@T0@ 25%,@C0@80 55%,transparent 75%);animation:fxFlash .7s ease-out forwards;pointer-events:none;z-index:9997}',
            '@keyframes fxFlash{0%{opacity:.95}100%{opacity:0}}',
        ]
    if sacudida:
        anim = (glow + ',' if glow else '') + 'fxWiggle 3s ease-in-out infinite'
        r += [
            '.ohana-env:not(.is-open){animation:@ANIM@}',
            '@keyframes fxWiggle{0%,86%,100%{rotate:0deg}89%{rotate:-3deg}92%{rotate:3deg}95%{rotate:-2deg}98%{rotate:1deg}}',
        ]
    reducida = []
    if destellos:
        reducida.append('#fx-festejo-capa,.fx-flash{display:none!important}')
    if sacudida:
        reducida.append('.ohana-env:not(.is-open){animation:@GLOW@}')
    r.append('@media (prefers-reduced-motion:reduce){' + ''.join(reducida) + '}')
    css = '\n'.join(r)
    for k, v in (('@C0@', c0), ('@C1@', c1), ('@T0@', mezcla(c0, .8)),
                 ('@ANIM@', (glow + ',' if glow else '') + 'fxWiggle 3s ease-in-out infinite'),
                 ('@GLOW@', glow or 'none')):
        css = css.replace(k, v)
    return '<style id="%s">\n%s\n</style>\n' % (ID_CSS, css)

def bloque_js(pal):
    cols = '[' + ','.join("'%s'" % c for c in pal) + ']'
    return ('<script id="%s">\n(function(){\ntry{\n'
            "if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;\n"
            "if(window.__fxFestejo)return;window.__fxFestejo=1;\n"
            "var capa=document.createElement('div');capa.id='fx-festejo-capa';document.body.appendChild(capa);\n"
            'var COL=%s;\n'
            'function rnd(a,b){return a+Math.random()*(b-a)}\n'
            'function pick(a){return a[Math.floor(Math.random()*a.length)]}\n'
            "function sparks(x,y,n){for(var i=0;i<n;i++){var s=document.createElement('i');s.className='fx-spark';var a=Math.PI*2*i/n+rnd(-.2,.2),d=rnd(50,120);\n"
            "  s.style.left=x+'px';s.style.top=y+'px';s.style.setProperty('--x',Math.cos(a)*d+'px');s.style.setProperty('--y',Math.sin(a)*d+'px');s.style.setProperty('--c',pick(COL));\n"
            "  capa.appendChild(s);setTimeout(function(e){e.remove()},900,s)}}\n"
            "function ring(x,y,sc){var r=document.createElement('div');r.className='fx-ring';r.style.left=x+'px';r.style.top=y+'px';r.style.setProperty('--s',sc||22);capa.appendChild(r);setTimeout(function(){r.remove()},1000)}\n"
            "function flash(x,y){var f=document.createElement('div');f.className='fx-flash';f.style.setProperty('--fx',x+'px');f.style.setProperty('--fy',y+'px');document.body.appendChild(f);setTimeout(function(){f.remove()},800)}\n"
            'var first=true,lastT=0;\n'
            "document.addEventListener('pointerdown',function(e){\n"
            ' var now=Date.now();if(now-lastT<140)return;lastT=now;\n'
            ' var x=e.clientX,y=e.clientY;\n'
            ' if(first){first=false;flash(x,y);ring(x,y,26);sparks(x,y,18)}\n'
            ' else{sparks(x,y,9)}\n'
            '},{passive:true,capture:true});\n'
            "}catch(err){console.warn('efectos',err)}\n})();\n</script>\n") % (ID_JS, cols)


# ---------- conflictos conocidos ----------
def quitar_clic_sonic(s):
    """Sonic trae su propio estallido de aros al clic y un barrido de luz al tocar el sobre; con los
    destellos unificados se duplicarían. Se quitan SOLO esos dos listeners (los aros que caen se quedan)."""
    n = 0
    p1 = re.compile(r"document\.addEventListener\('pointerdown',function\(e\)\{\n for\(var n=0;n<8;n\+\+\).*?\},\{passive:true\}\);\n", re.S)
    s, k = p1.subn('', s); n += k
    p2 = re.compile(r"document\.addEventListener\('click',function\(e\)\{var t=e\.target&&e\.target\.closest&&e\.target\.closest\('\.ohana-env'\);.*?\},true\);\n", re.S)
    s, k = p2.subn('', s); n += k
    return s, n


def jsok(s):
    """node --check SOLO de los scripts que este programa agrega o modifica (el paquete de React trae
    textos con '<\\/script>' que confunden a un análisis por expresión regular)."""
    bad = []
    for sid in (ID_JS, 'sonic-fx-js'):
        m = re.search(r'<script id="%s">(.*?)</script>' % re.escape(sid), s, re.S)
        if not m:
            continue
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(m.group(1)); p = f.name
        try:
            r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
            if r.returncode:
                bad.append('%s: %s' % (sid, r.stderr[:200]))
        except FileNotFoundError:
            return None
        finally:
            os.unlink(p)
    return bad


def quitar_bloques(s):
    s = re.sub(r'<style id="%s">.*?</style>\n?' % ID_CSS, '', s, flags=re.S)
    s = re.sub(r'<script id="%s">.*?</script>\n?' % ID_JS, '', s, flags=re.S)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('entrada'); ap.add_argument('salida')
    ap.add_argument('--colores', help="lista separada por comas (#hex). Por defecto se extraen del sobre")
    ap.add_argument('--glow', help="animación de brillo a conservar, p. ej. 'ohanaGlow 2.6s ease-in-out infinite' (por defecto se detecta)")
    ap.add_argument('--sin-sacudida', action='store_true'); ap.add_argument('--sin-destellos', action='store_true')
    ap.add_argument('--quitar', action='store_true')
    a = ap.parse_args()

    s = open(a.entrada, encoding='utf-8').read()
    if 'id="cp-efectos"' in s:
        print('= %s ya trae los efectos de referencia (cp-efectos). No se toca.' % os.path.basename(a.entrada))
        if os.path.abspath(a.entrada) != os.path.abspath(a.salida):
            open(a.salida, 'w', encoding='utf-8').write(s)
        return 0
    s = quitar_bloques(s)
    if a.quitar:
        open(a.salida, 'w', encoding='utf-8').write(s)
        print('✓ bloque de efectos quitado'); return 0
    if s.count('</body>') != 1:
        print('✗ el HTML debe tener exactamente un </body>'); return 1
    if 'ohana-env' not in s:
        print('✗ no se encontró el sobre (.ohana-env): ¿es una invitación de esta familia?'); return 1

    sacudida, destellos = not a.sin_sacudida, not a.sin_destellos
    if a.colores:
        base = [norm(c.strip()) for c in a.colores.split(',') if HEX.fullmatch(c.strip())]
        pal = componer_paleta(base) if len(base) < 4 else base[:9]
    else:
        pal = componer_paleta(extraer_paleta(s))
    glow = a.glow if a.glow else detectar_glow(s)

    notas = []
    if 'id="sonic-fx-js"' in s and destellos:
        s, n = quitar_clic_sonic(s)
        notas.append('Sonic: se quitaron %d listener(s) propios de clic (aros + barrido) para no duplicar' % n)

    bloques = bloque_css(pal, glow, sacudida, destellos) + (bloque_js(pal) if destellos else '')
    s = s.replace('</body>', bloques + '</body>')

    bad = jsok(s)
    if bad:
        print('✗ error de sintaxis JS:', bad[0]); return 1
    open(a.salida, 'w', encoding='utf-8').write(s)
    print('✓ %s' % os.path.basename(a.salida))
    print('   colores de los destellos:', ' '.join(pal))
    print('   brillo del sobre conservado:', glow or '(ninguno)')
    print('   sacudida del sobre: %s · destellos al clic: %s' % ('sí' if sacudida else 'no', 'sí' if destellos else 'no'))
    for n in notas:
        print('   ·', n)
    if bad is None:
        print('   (aviso: node no está instalado; no se verificó la sintaxis)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
