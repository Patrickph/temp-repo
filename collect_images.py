import csv
import io
import os
import re
import time
import json
import shutil
import zipfile
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageOps, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
OUT = BASE / 'output'
CACHE = BASE / '.cache'
OUT.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'ProductImageCollector/1.0 (temporary catalog build)',
    'Accept': 'application/json,image/*,*/*;q=0.8',
})

PT_EN_HINTS = {
    'mini impressora térmica': 'mini thermal printer product',
    'mini seladora de embalagens': 'mini bag sealer product',
    'carregador magnético sem fio': 'magnetic wireless charger product',
    'fone bluetooth tws': 'wireless earbuds product',
    'luminária sunset ou rgb': 'sunset lamp rgb product',
    'copo térmico grande': 'large insulated tumbler product',
    'organizador de cabos': 'cable organizer product',
    'mini aspirador portátil': 'mini handheld vacuum product',
    'liquidificador portátil': 'portable blender product',
    'caixa de som bluetooth': 'bluetooth speaker product',
    'smartwatch': 'smartwatch product',
    'projetor portátil': 'portable projector product',
    'extratora portátil para sofá': 'portable upholstery cleaner product',
    'air fryer': 'air fryer product',
    'robô aspirador básico': 'robot vacuum cleaner product',
    'robô aspirador premium': 'premium robot vacuum product',
    'escova secadora': 'hot air brush product',
    'kit de ferramentas sem fio': 'cordless power tool kit product',
    'smartphone': 'smartphone product',
    'notebook': 'laptop product',
    'console de videogame': 'video game console product',
    'smart tv': 'smart television product',
    'chuteira': 'soccer cleats product',
    'luva de goleiro profissional': 'goalkeeper gloves product',
    'steam deck oled': 'Steam Deck OLED product',
    'rog ally': 'ROG Ally handheld product',
    'gift card steam': 'Steam gift card',
    'gift card google play': 'Google Play gift card',
    'gift card robux': 'Roblox gift card',
    'gift card apple': 'Apple gift card',
    'gift card playstation': 'PlayStation gift card',
    'gift card xbox': 'Xbox gift card',
    'gift card nintendo': 'Nintendo gift card',
    'hardware wallet': 'cryptocurrency hardware wallet product',
    'apple airtag': 'Apple AirTag product',
    'apple airpods': 'Apple AirPods product',
    'apple watch': 'Apple Watch product',
    'apple pencil': 'Apple Pencil product',
    'apple ipad básico': 'Apple iPad product',
    'apple magic mouse': 'Apple Magic Mouse product',
    'teclado apple magic keyboard': 'Apple Magic Keyboard product',
    'iphone pro da geração atual': 'iPhone Pro product',
    'macbook air': 'MacBook Air product',
    'macbook pro': 'MacBook Pro product',
    'ipad pro': 'iPad Pro product',
    'airpods max': 'AirPods Max product',
    'scanner obd2': 'OBD2 scanner product',
    'dashcam': 'dash camera product',
    'ring light': 'ring light product',
    'selfie stick': 'selfie stick product',
    'gimbal para smartphone': 'smartphone gimbal product',
    'dji osmo pocket': 'DJI Osmo Pocket product',
    'sony zv-e10 ii': 'Sony ZV-E10 II camera product',
    'canon powershot v1': 'Canon PowerShot V1 camera product',
    'insta360 x5': 'Insta360 X5 camera product',
}


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', s.lower()).strip()


def slug(s: str) -> str:
    s = norm(s)
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s[:90] or 'produto'


def translate_hint(name: str) -> str:
    n = norm(name)
    for key, value in PT_EN_HINTS.items():
        if key in n:
            return value
    return f'{name} product'


def search_openverse(query: str):
    url = 'https://api.openverse.org/v1/images/'
    params = {
        'q': query,
        'page_size': 20,
        'license_type': 'commercial',
        'mature': 'false',
    }
    r = SESSION.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    results = data.get('results', [])
    ranked = []
    for item in results:
        img_url = item.get('thumbnail') or item.get('url')
        if not img_url or img_url.lower().endswith('.svg'):
            continue
        width = item.get('width') or 0
        height = item.get('height') or 0
        score = min(width, height)
        ranked.append((score, item))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in ranked]


def search_wikimedia(query: str):
    api = 'https://commons.wikimedia.org/w/api.php'
    params = {
        'action': 'query',
        'generator': 'search',
        'gsrsearch': query,
        'gsrnamespace': 6,
        'gsrlimit': 15,
        'prop': 'imageinfo',
        'iiprop': 'url|extmetadata',
        'iiurlwidth': 800,
        'format': 'json',
        'origin': '*',
    }
    r = SESSION.get(api, params=params, timeout=30)
    r.raise_for_status()
    pages = (r.json().get('query') or {}).get('pages') or {}
    out = []
    for p in pages.values():
        info = (p.get('imageinfo') or [{}])[0]
        u = info.get('thumburl') or info.get('url')
        if not u or u.lower().endswith('.svg'):
            continue
        ext = info.get('extmetadata') or {}
        out.append({
            'thumbnail': u,
            'foreign_landing_url': info.get('descriptionurl') or p.get('canonicalurl'),
            'creator': (ext.get('Artist') or {}).get('value', ''),
            'license': (ext.get('LicenseShortName') or {}).get('value', ''),
            'source': 'Wikimedia Commons',
            'title': p.get('title', ''),
        })
    return out


def download_image(url: str) -> Image.Image:
    r = SESSION.get(url, timeout=45, stream=True)
    r.raise_for_status()
    content = r.content
    if len(content) < 3000:
        raise ValueError('imagem muito pequena')
    im = Image.open(io.BytesIO(content))
    im.load()
    return im.convert('RGB')


def make_placeholder(name: str, box: str) -> Image.Image:
    im = Image.new('RGB', (700, 700), '#f8fafc')
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((35, 35, 665, 665), radius=28, outline='#cbd5e1', width=5, fill='#ffffff')
    d.text((70, 90), box, fill='#64748b')
    words = name.split()
    lines, line = [], ''
    for word in words:
        test = (line + ' ' + word).strip()
        if len(test) > 24:
            lines.append(line)
            line = word
        else:
            line = test
    if line: lines.append(line)
    y = 260
    for ln in lines[:4]:
        d.text((80, y), ln, fill='#0f172a')
        y += 45
    d.text((80, 570), 'Imagem pendente de revisão', fill='#ef4444')
    return im


def fit_web(im: Image.Image) -> Image.Image:
    canvas = Image.new('RGB', (700, 700), 'white')
    fitted = ImageOps.contain(im, (620, 620), method=Image.Resampling.LANCZOS)
    x = (700 - fitted.width)//2
    y = (700 - fitted.height)//2
    canvas.paste(fitted, (x, y))
    return canvas


def main():
    manifest = []
    cache_by_query = {}
    with open(BASE / 'products.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    for idx, row in enumerate(rows, start=1):
        name = row['premio'].strip()
        box = row['caixa'].strip()
        level = row['nivel'].strip()
        box_dir = OUT / slug(box)
        box_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(row['id']):03d}-{slug(name)}.webp"
        dest = box_dir / filename
        query = translate_hint(name)
        key = norm(query)
        status = 'ok'
        source = ''
        source_page = ''
        image_url = ''
        creator = ''
        license_name = ''
        title = ''
        error = ''

        print(f'[{idx}/{len(rows)}] {box} / {name}', flush=True)
        try:
            if key in cache_by_query:
                cached = cache_by_query[key]
                shutil.copy2(cached['path'], dest)
                source = cached['source']
                source_page = cached['source_page']
                image_url = cached['image_url']
                creator = cached['creator']
                license_name = cached['license']
                title = cached['title']
                status = 'reutilizada'
            else:
                candidates = []
                try:
                    candidates = search_openverse(query)
                except Exception as exc:
                    error += f'Openverse: {exc}; '
                if not candidates:
                    try:
                        candidates = search_wikimedia(query)
                    except Exception as exc:
                        error += f'Wikimedia: {exc}; '
                chosen = None
                im = None
                for item in candidates[:12]:
                    try:
                        u = item.get('thumbnail') or item.get('url')
                        im = download_image(u)
                        if min(im.size) < 250:
                            continue
                        chosen = item
                        break
                    except Exception as exc:
                        error += f'download: {exc}; '
                if chosen is None or im is None:
                    status = 'placeholder'
                    im = make_placeholder(name, box)
                else:
                    source = chosen.get('source') or 'Openverse'
                    source_page = chosen.get('foreign_landing_url') or chosen.get('detail_url') or ''
                    image_url = chosen.get('thumbnail') or chosen.get('url') or ''
                    creator = chosen.get('creator') or ''
                    license_name = chosen.get('license') or chosen.get('license_version') or ''
                    title = chosen.get('title') or ''
                fit_web(im).save(dest, 'WEBP', quality=88, method=6)
                cache_by_query[key] = {
                    'path': dest,
                    'source': source,
                    'source_page': source_page,
                    'image_url': image_url,
                    'creator': creator,
                    'license': license_name,
                    'title': title,
                }
        except Exception as exc:
            status = 'erro-placeholder'
            error += str(exc)
            make_placeholder(name, box).save(dest, 'WEBP', quality=88)

        manifest.append({
            'id': row['id'],
            'caixa': box,
            'nivel': level,
            'premio': name,
            'arquivo': str(dest.relative_to(OUT)),
            'status': status,
            'fonte': source,
            'pagina_origem': source_page,
            'url_imagem': image_url,
            'criador': re.sub('<[^>]+>', '', creator),
            'licenca': re.sub('<[^>]+>', '', license_name),
            'titulo_origem': title,
            'erro': error[:500],
        })
        time.sleep(0.15)

    fields = list(manifest[0].keys())
    with open(OUT / 'manifest.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(manifest)
    with open(OUT / 'manifest.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    readme = '''PACOTE DE IMAGENS DE PRODUTOS\n\nAs imagens foram pesquisadas em fontes públicas com metadados de origem e licença.\nConsulte manifest.csv antes de publicar. Algumas entradas podem ser placeholders quando não foi encontrada uma imagem adequada.\nAs imagens são representativas da categoria do produto e não garantem correspondência com um anúncio específico.\n'''
    (OUT / 'LEIA-ME.txt').write_text(readme, encoding='utf-8')

    zip_path = BASE / 'product-images.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob('*'):
            if p.is_file():
                z.write(p, p.relative_to(OUT))
    print(f'ZIP criado: {zip_path} ({zip_path.stat().st_size} bytes)', flush=True)

if __name__ == '__main__':
    main()
