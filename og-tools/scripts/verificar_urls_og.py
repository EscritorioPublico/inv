#!/usr/bin/env python3
"""Comprueba que las miniaturas publicadas (og/manifest.json) ya responden como imagen JPEG en internet.
GitHub Pages tarda 1-3 minutos en publicar un commit nuevo; este script reintenta hasta --espera segundos.
Uso: python3 verificar_urls_og.py <carpeta> [--slugs a,b,c] [--espera 480]
Sale con 1 (y deja un aviso ::warning:: para GitHub Actions) si alguna no apareció a tiempo."""
import argparse, glob, json, os, sys, time, urllib.request, urllib.error


def ok(url):
    try:
        rq = urllib.request.Request(url, headers={'User-Agent': 'WhatsApp/2.23 verificador-og'})
        with urllib.request.urlopen(rq, timeout=20) as r:
            r.read()
            return r.status == 200 and (r.headers.get('Content-Type') or '').startswith('image/'), r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)[:60]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('carpeta'); ap.add_argument('--slugs', default=''); ap.add_argument('--espera', type=int, default=480)
    a = ap.parse_args()
    pedidos = [x for x in a.slugs.split(',') if x]
    urls = {}
    for m in glob.glob(os.path.join(a.carpeta, '**', 'og', 'manifest.json'), recursive=True):
        for slug, fila in json.load(open(m, encoding='utf-8')).items():
            if (not pedidos or slug in pedidos) and fila.get('url'):
                urls[slug] = fila['url']
    if not urls:
        print('Nada que verificar.'); return 0
    pendientes, fin = dict(urls), time.time() + a.espera
    while pendientes and time.time() < fin:
        for slug, url in list(pendientes.items()):
            bien, estado = ok(url)
            if bien:
                print('✔ %-18s %s' % (slug, url)); del pendientes[slug]
        if pendientes:
            time.sleep(20)
    for slug, url in pendientes.items():
        print('✘ %-18s %s  (%s)' % (slug, url, ok(url)[1]))
        print('::warning::La miniatura de %s aún no responde en %s. Revisa Settings → Pages (fuente: "Deploy from a branch"), '
              'que la URL del sitio sea la correcta (variable URL_SITIO / RAIZ_PAGES) y la pestaña Actions → pages-build-deployment.' % (slug, url))
    return 1 if pendientes else 0


if __name__ == '__main__':
    sys.exit(main())
