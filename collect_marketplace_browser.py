import asyncio
import csv
import io
import json
import re
import shutil
import time
import unicodedata
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageDraw, ImageOps
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
})

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "ou", "e", "em", "um", "uma",
    "o", "a", "os", "as", "kit", "produto", "modelo", "atual", "geracao", "premium", "basico",
    "basica", "grande", "completo", "completa", "alto", "alta", "valor", "novo", "nova", "reais", "r",
}

ALIASES = {
    "console com ea sports fc": "console videogame ea sports fc",
    "pc gamer completo": "computador pc gamer completo",
    "pc portatil gamer": "console portatil gamer",
    "smartphone pro para conteudo": "smartphone camera profissional",
    "kit completo de estudio": "kit estudio fotografia video",
    "gift card robux de alto valor": "gift card roblox robux",
    "iphone pro da geracao atual": "iphone pro",
    "voucher de r 50 em bitcoin": "voucher bitcoin",
    "voucher de r 100 em usdt": "voucher usdt",
    "voucher de r 500 em bitcoin": "voucher bitcoin",
    "voucher de r 500 em ethereum": "voucher ethereum",
    "voucher de r 500 em usdt": "voucher usdt",
    "voucher de r 2 000 em bitcoin": "voucher bitcoin",
    "voucher de r 5 000 em bitcoin ou ethereum": "voucher bitcoin ethereum",
}

SYNONYMS = {
    "smartphone": {"smartphone", "celular"}, "celular": {"smartphone", "celular"},
    "notebook": {"notebook", "laptop"}, "laptop": {"notebook", "laptop"},
    "tv": {"tv", "televisao", "smarttv"}, "televisao": {"tv", "televisao", "smarttv"},
    "fone": {"fone", "earbud", "earbuds", "headphone", "headset"},
    "console": {"console", "videogame"}, "videogame": {"console", "videogame"},
    "controle": {"controle", "joystick", "gamepad"}, "robo": {"robo", "robot"},
    "relogio": {"relogio", "watch"}, "cartao": {"cartao", "gift", "card"},
    "gift": {"gift", "card", "cartao"}, "card": {"gift", "card", "cartao"},
    "mousepad": {"mousepad", "deskmat"}, "caixa": {"caixa", "speaker"}, "som": {"som", "speaker"},
}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:100] or "produto"


def tokens(text: str) -> list[str]:
    return [x for x in norm(text).split() if x not in STOPWORDS and len(x) > 1 and not x.isdigit()]


def query_for(name: str) -> str:
    n = norm(name)
    if n in ALIASES:
        return ALIASES[n]
    q = re.sub(r"R\$\s*[\d\.]+(?:,\d+)?", "", name, flags=re.I)
    q = re.sub(r"\b(?:da geração atual|de alto valor|premium|básico|básica|profissional)\b", "", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


def score(product: str, title: str) -> float:
    p_tokens = tokens(product)
    t_tokens = set(tokens(title))
    if not p_tokens or not t_tokens:
        return 0.0
    matched = 0
    for token in p_tokens:
        options = SYNONYMS.get(token, {token})
        if options & t_tokens:
            matched += 1
    coverage = matched / len(p_tokens)
    if matched == 0:
        return 0.0
    similarity = SequenceMatcher(None, norm(product), norm(title)).ratio()
    result = coverage * 0.78 + similarity * 0.22
    protected = {"apple", "iphone", "ipad", "macbook", "airpods", "steam", "roblox", "playstation", "xbox", "nintendo", "dji", "sony", "canon", "insta360", "bitcoin", "ethereum", "usdt", "cartier", "dyson"}
    req = set(norm(product).split()) & protected
    if req and not req.issubset(set(norm(title).split())):
        result -= 0.35
    return max(0.0, min(1.0, result))


def threshold(product: str) -> float:
    n = len(tokens(product))
    return 0.58 if n <= 1 else 0.49 if n == 2 else 0.43


def normalize_image_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("http://", "https://")
    url = url.replace("D_Q_NP_", "D_NQ_NP_")
    return url


async def search_ml(page, query: str) -> list[dict]:
    url = "https://lista.mercadolivre.com.br/" + quote_plus(query).replace("+", "-")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1800)
    except PlaywrightTimeoutError:
        pass
    return await page.evaluate("""
    () => {
      const cards = Array.from(document.querySelectorAll('li.ui-search-layout__item, div.ui-search-result, div.poly-card'));
      return cards.slice(0, 25).map(card => {
        const link = card.querySelector('a.poly-component__title, a.ui-search-link, a[href*="mercadolivre.com.br"]');
        const titleEl = card.querySelector('.poly-component__title, .ui-search-item__title, h2, h3');
        const img = card.querySelector('img');
        let image = '';
        if (img) image = img.getAttribute('data-src') || img.getAttribute('data-lazy') || img.currentSrc || img.src || '';
        return {title: (titleEl?.textContent || link?.textContent || '').trim(), listing_url: link?.href || '', image_url: image};
      }).filter(x => x.title && x.image_url);
    }
    """)


async def search_shopee(page, query: str) -> list[dict]:
    url = "https://shopee.com.br/search?keyword=" + quote_plus(query)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2800)
        await page.mouse.wheel(0, 700)
        await page.wait_for_timeout(900)
    except PlaywrightTimeoutError:
        pass
    return await page.evaluate("""
    () => {
      const cards = Array.from(document.querySelectorAll('[data-sqe="item"], .shopee-search-item-result__item'));
      return cards.slice(0, 25).map(card => {
        const link = card.querySelector('a[href]');
        const img = card.querySelector('img');
        const texts = Array.from(card.querySelectorAll('div, span')).map(e => (e.textContent || '').trim()).filter(t => t.length > 5 && t.length < 220);
        const title = texts.sort((a,b) => b.length-a.length)[0] || (link?.textContent || '').trim();
        let image = '';
        if (img) image = img.getAttribute('data-src') || img.currentSrc || img.src || '';
        return {title, listing_url: link?.href || '', image_url: image};
      }).filter(x => x.title && x.image_url);
    }
    """)


def download(candidate: dict) -> Image.Image:
    headers = dict(HTTP.headers)
    if candidate.get("listing_url"):
        headers["Referer"] = candidate["listing_url"]
    response = HTTP.get(normalize_image_url(candidate["image_url"]), headers=headers, timeout=35)
    response.raise_for_status()
    if len(response.content) < 3000:
        raise ValueError("imagem pequena")
    image = Image.open(io.BytesIO(response.content))
    image.load()
    if min(image.size) < 160:
        raise ValueError("resolucao insuficiente")
    return image.convert("RGB")


def fit(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (700, 700), "white")
    item = ImageOps.contain(image, (650, 650), Image.Resampling.LANCZOS)
    canvas.paste(item, ((700-item.width)//2, (700-item.height)//2))
    return canvas


def placeholder(name: str, box: str) -> Image.Image:
    image = Image.new("RGB", (700, 700), "#f8fafc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35,35,665,665), radius=28, fill="white", outline="#cbd5e1", width=5)
    draw.text((70,90), box, fill="#64748b")
    words, lines, line = name.split(), [], ""
    for word in words:
        test = (line + " " + word).strip()
        if len(test) > 25:
            lines.append(line); line = word
        else:
            line = test
    if line: lines.append(line)
    y = 250
    for value in lines[:5]:
        draw.text((75,y), value, fill="#0f172a"); y += 44
    draw.text((75,575), "SEM ANUNCIO CONFIAVEL", fill="#dc2626")
    return image


async def main():
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir()
    rows = list(csv.DictReader(open(BASE / "products.csv", encoding="utf-8")))
    manifest, cache = [], {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(locale="pt-BR", viewport={"width": 1440, "height": 1000}, user_agent=HTTP.headers["User-Agent"])
        ml_page = await context.new_page()
        shopee_page = await context.new_page()

        for index, row in enumerate(rows, 1):
            pid, box, level, name = int(row["id"]), row["caixa"].strip(), row["nivel"].strip(), row["premio"].strip()
            directory = OUT / slug(box); directory.mkdir(parents=True, exist_ok=True)
            dest = directory / f"{pid:03d}-{slug(name)}.webp"
            key = norm(name)
            print(f"[{index}/{len(rows)}] {box} / {name}", flush=True)

            if key in cache:
                shutil.copy2(cache[key]["path"], dest)
                rec = dict(cache[key]["record"])
                rec.update({"id": pid, "caixa": box, "nivel": level, "premio": name, "arquivo": str(dest.relative_to(OUT)), "status": "reutilizada"})
                manifest.append(rec)
                continue

            query = query_for(name)
            candidates, errors = [], []
            # Alterna a prioridade para que o pacote tenha resultados dos dois marketplaces.
            sources = [("Mercado Livre", ml_page, search_ml), ("Shopee", shopee_page, search_shopee)]
            if pid % 2 == 0:
                sources.reverse()
            for marketplace, page, fn in sources:
                try:
                    found = await fn(page, query)
                    for item in found:
                        item["marketplace"] = marketplace
                    candidates.extend(found)
                except Exception as exc:
                    errors.append(f"{marketplace}: {exc}")

            candidates.sort(key=lambda x: score(name, x["title"]), reverse=True)
            chosen = None; image = None
            for item in candidates[:20]:
                item_score = score(name, item["title"])
                if item_score < threshold(name): break
                try:
                    image = download(item); chosen = item; chosen["score"] = item_score; break
                except Exception as exc:
                    errors.append(f"download {item.get('marketplace')}: {exc}")

            if chosen and image:
                fit(image).save(dest, "WEBP", quality=88, method=6)
                rec = {"id": pid, "caixa": box, "nivel": level, "premio": name, "arquivo": str(dest.relative_to(OUT)), "status": "ok", "marketplace": chosen["marketplace"], "titulo_anuncio": chosen["title"], "url_anuncio": chosen["listing_url"], "url_imagem": normalize_image_url(chosen["image_url"]), "score_correspondencia": round(chosen["score"],4), "erros": " | ".join(errors)[:1200]}
            else:
                placeholder(name, box).save(dest, "WEBP", quality=88, method=6)
                rec = {"id": pid, "caixa": box, "nivel": level, "premio": name, "arquivo": str(dest.relative_to(OUT)), "status": "sem-correspondencia", "marketplace": "", "titulo_anuncio": "", "url_anuncio": "", "url_imagem": "", "score_correspondencia": 0, "erros": " | ".join(errors)[:1200]}
            manifest.append(rec); cache[key] = {"path": dest, "record": rec}
            await asyncio.sleep(0.18)

        await browser.close()

    fields = list(manifest[0].keys())
    with open(OUT/"manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(manifest)
    (OUT/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"total":len(manifest), "ok":sum(x["status"] in {"ok","reutilizada"} for x in manifest), "sem_correspondencia":sum(x["status"]=="sem-correspondencia" for x in manifest), "mercado_livre":sum(x["marketplace"]=="Mercado Livre" for x in manifest), "shopee":sum(x["marketplace"]=="Shopee" for x in manifest)}
    (OUT/"resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"LEIA-ME.txt").write_text("Imagens coletadas de anuncios reais da Shopee e Mercado Livre. Confira manifest.csv antes de publicar. Entradas sem correspondencia confiavel foram mantidas como placeholder.\n", encoding="utf-8")
    zip_path = BASE/"product-images-marketplaces.zip"
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
        for path in OUT.rglob("*"):
            if path.is_file(): z.write(path,path.relative_to(OUT))
    print(json.dumps(summary, ensure_ascii=False), flush=True)

if __name__ == "__main__": asyncio.run(main())
