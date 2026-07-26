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

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    "Accept": "application/json,text/html,image/avif,image/webp,image/apng,*/*;q=0.8",
})

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "ou", "e", "em",
    "um", "uma", "o", "a", "os", "as", "kit", "produto", "modelo", "atual",
    "geracao", "premium", "basico", "basica", "grande", "completo", "completa",
    "alto", "alta", "valor", "novo", "nova", "reais", "r", "brasil",
}

SYNONYMS = {
    "smartphone": {"smartphone", "celular", "telefone"},
    "celular": {"smartphone", "celular", "telefone"},
    "notebook": {"notebook", "laptop"},
    "laptop": {"notebook", "laptop"},
    "televisao": {"televisao", "tv", "smarttv"},
    "tv": {"televisao", "tv", "smarttv"},
    "fone": {"fone", "earbud", "earbuds", "headphone", "headset"},
    "earbuds": {"fone", "earbud", "earbuds"},
    "console": {"console", "videogame", "video-game"},
    "videogame": {"console", "videogame", "video-game"},
    "aspirador": {"aspirador", "vacuum"},
    "carregador": {"carregador", "charger"},
    "caixa": {"caixa", "speaker"},
    "som": {"som", "speaker"},
    "chuteira": {"chuteira", "society", "futsal", "campo"},
    "camisa": {"camisa", "camiseta", "jersey"},
    "controle": {"controle", "gamepad", "joystick"},
    "mousepad": {"mousepad", "deskmat"},
    "gift": {"gift", "cartao", "card"},
    "card": {"gift", "cartao", "card"},
    "cartao": {"gift", "cartao", "card"},
    "robo": {"robo", "robot"},
    "relogio": {"relogio", "watch"},
    "airpods": {"airpods"},
    "ipad": {"ipad"},
    "iphone": {"iphone"},
    "macbook": {"macbook"},
}

QUERY_ALIASES = {
    "console com ea sports fc": "console videogame ea sports fc bundle",
    "pc gamer completo": "computador pc gamer completo",
    "pc portatil gamer": "console portatil gamer pc",
    "smartphone pro para conteudo": "smartphone camera profissional",
    "kit completo de estudio": "kit estudio fotografia video",
    "viagem para assistir a uma partida": "voucher viagem futebol",
    "ingresso para jogo de futebol": "ingresso futebol",
    "voucher para revisao automotiva": "voucher revisao automotiva",
    "voucher para estetica automotiva completa": "voucher estetica automotiva",
    "voucher de r 50 em bitcoin": "voucher bitcoin 50 reais",
    "voucher de r 100 em usdt": "voucher usdt 100 reais",
    "voucher de r 500 em bitcoin": "voucher bitcoin 500 reais",
    "voucher de r 500 em ethereum": "voucher ethereum 500 reais",
    "voucher de r 500 em usdt": "voucher usdt 500 reais",
    "voucher de r 2 000 em bitcoin": "voucher bitcoin 2000 reais",
    "voucher de r 5 000 em bitcoin ou ethereum": "voucher bitcoin ethereum 5000 reais",
}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:100] or "produto"


def meaningful_tokens(text: str) -> list[str]:
    return [t for t in norm(text).split() if t not in STOPWORDS and len(t) > 1 and not t.isdigit()]


def variants(name: str) -> list[str]:
    normalized = norm(name)
    values = []
    alias = QUERY_ALIASES.get(normalized)
    if alias:
        values.append(alias)
    values.append(name)

    simplified = re.sub(r"\([^)]*\)", " ", name)
    simplified = re.sub(r"R\$\s*[\d\.]+(?:,\d+)?", " ", simplified, flags=re.I)
    simplified = re.sub(
        r"\b(?:da geração atual|de alto valor|premium|básico|básica|completo|completa|profissional)\b",
        " ", simplified, flags=re.I,
    )
    simplified = re.sub(r"\s+", " ", simplified).strip(" -")
    if norm(simplified) != normalized:
        values.append(simplified)

    # gift cards funcionam melhor quando a busca mantém explicitamente “gift card”.
    if "gift card" in normalized and not normalized.startswith("gift card"):
        values.append("gift card " + simplified)

    out = []
    seen = set()
    for value in values:
        key = norm(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:3]


def token_matches(token: str, title_tokens: set[str]) -> bool:
    options = SYNONYMS.get(token, {token})
    return any(option in title_tokens for option in options)


def relevance_score(product: str, title: str) -> float:
    target_tokens = meaningful_tokens(product)
    title_norm = norm(title)
    title_tokens = set(meaningful_tokens(title))
    if not target_tokens or not title_tokens:
        return 0.0

    matched = sum(1 for token in target_tokens if token_matches(token, title_tokens))
    coverage = matched / len(target_tokens)
    sequence = SequenceMatcher(None, norm(product), title_norm).ratio()

    # Para buscas curtas, pelo menos o termo principal precisa aparecer.
    if len(target_tokens) == 1 and matched == 0:
        return 0.0
    if len(target_tokens) >= 2 and matched == 0:
        return 0.0

    score = 0.75 * coverage + 0.25 * sequence

    # Bônus para correspondência de marcas importantes.
    protected = {
        "apple", "iphone", "ipad", "macbook", "airpods", "steam", "roblox",
        "playstation", "xbox", "nintendo", "dji", "sony", "canon", "insta360",
        "bitcoin", "ethereum", "usdt", "cartier", "dyson",
    }
    product_words = set(norm(product).split())
    title_words = set(title_norm.split())
    required_brands = protected & product_words
    if required_brands and not required_brands.issubset(title_words):
        score -= 0.35

    return max(0.0, min(1.0, score))


def minimum_score(product: str) -> float:
    count = len(meaningful_tokens(product))
    if count <= 1:
        return 0.62
    if count == 2:
        return 0.52
    return 0.46


def search_mercado_livre(query: str) -> list[dict]:
    url = "https://api.mercadolibre.com/sites/MLB/search"
    response = SESSION.get(url, params={"q": query, "limit": 40}, timeout=30)
    response.raise_for_status()
    results = []
    for item in response.json().get("results", []):
        title = item.get("title") or ""
        image = (item.get("thumbnail") or "").replace("http://", "https://")
        image = image.replace("D_Q_NP_", "D_NQ_NP_")
        if not image:
            continue
        results.append({
            "marketplace": "Mercado Livre",
            "title": title,
            "listing_url": item.get("permalink") or "",
            "image_url": image,
            "item_id": item.get("id") or "",
        })
    return results


def warm_shopee_session() -> None:
    try:
        SESSION.get("https://shopee.com.br/", timeout=20)
    except Exception:
        pass


def search_shopee(query: str) -> list[dict]:
    url = "https://shopee.com.br/api/v4/search/search_items"
    params = {
        "by": "relevancy",
        "keyword": query,
        "limit": 40,
        "newest": 0,
        "order": "desc",
        "page_type": "search",
        "scenario": "PAGE_GLOBAL_SEARCH",
        "version": 2,
    }
    headers = {
        "Referer": "https://shopee.com.br/search?keyword=" + quote_plus(query),
        "X-API-SOURCE": "pc",
        "X-Requested-With": "XMLHttpRequest",
    }
    response = SESSION.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()
    results = []
    for wrapper in payload.get("items") or []:
        basic = wrapper.get("item_basic") or wrapper.get("item") or wrapper
        title = basic.get("name") or basic.get("title") or ""
        image_hash = basic.get("image") or ""
        images = basic.get("images") or []
        if not image_hash and images:
            image_hash = images[0]
        if not title or not image_hash:
            continue
        shop_id = basic.get("shopid") or basic.get("shop_id") or ""
        item_id = basic.get("itemid") or basic.get("item_id") or ""
        results.append({
            "marketplace": "Shopee",
            "title": title,
            "listing_url": f"https://shopee.com.br/product/{shop_id}/{item_id}" if shop_id and item_id else "",
            "image_url": f"https://down-br.img.susercontent.com/file/{image_hash}",
            "image_fallback": f"https://cf.shopee.com.br/file/{image_hash}",
            "item_id": str(item_id),
        })
    return results


def download_image(candidate: dict) -> Image.Image:
    urls = [candidate.get("image_url"), candidate.get("image_fallback")]
    last_error = None
    for url in urls:
        if not url:
            continue
        try:
            response = SESSION.get(url, timeout=35)
            response.raise_for_status()
            if len(response.content) < 3500:
                raise ValueError("arquivo de imagem muito pequeno")
            image = Image.open(io.BytesIO(response.content))
            image.load()
            if min(image.size) < 180:
                raise ValueError("resolução insuficiente")
            return image.convert("RGB")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "falha ao baixar imagem"))


def fit_web(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (700, 700), "white")
    fitted = ImageOps.contain(image, (650, 650), method=Image.Resampling.LANCZOS)
    x = (700 - fitted.width) // 2
    y = (700 - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return canvas


def make_missing(name: str, box: str) -> Image.Image:
    image = Image.new("RGB", (700, 700), "#f8fafc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, 665, 665), radius=28, fill="white", outline="#cbd5e1", width=5)
    draw.text((70, 85), box, fill="#64748b")
    words = name.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if len(test) > 24:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    y = 250
    for value in lines[:5]:
        draw.text((75, y), value, fill="#0f172a")
        y += 44
    draw.text((75, 575), "SEM ANÚNCIO CONFIÁVEL", fill="#dc2626")
    return image


def collect_candidates(name: str) -> tuple[list[dict], list[str]]:
    all_candidates = []
    errors = []
    for query in variants(name):
        try:
            all_candidates.extend(search_mercado_livre(query))
        except Exception as exc:
            errors.append(f"Mercado Livre ({query}): {exc}")
        try:
            all_candidates.extend(search_shopee(query))
        except Exception as exc:
            errors.append(f"Shopee ({query}): {exc}")

        ranked = sorted(
            all_candidates,
            key=lambda item: relevance_score(name, item.get("title", "")),
            reverse=True,
        )
        if ranked and relevance_score(name, ranked[0].get("title", "")) >= minimum_score(name) + 0.08:
            break
        time.sleep(0.15)
    return all_candidates, errors


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(exist_ok=True)
    warm_shopee_session()

    with open(BASE / "products.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    manifest = []
    cache = {}

    for index, row in enumerate(rows, start=1):
        product_id = int(row["id"])
        box = row["caixa"].strip()
        level = row["nivel"].strip()
        name = row["premio"].strip()
        cache_key = norm(name)
        box_dir = OUT / slug(box)
        box_dir.mkdir(parents=True, exist_ok=True)
        destination = box_dir / f"{product_id:03d}-{slug(name)}.webp"

        print(f"[{index}/{len(rows)}] {box} / {name}", flush=True)

        if cache_key in cache:
            cached = cache[cache_key]
            shutil.copy2(cached["path"], destination)
            record = dict(cached["record"])
            record.update({
                "id": product_id,
                "caixa": box,
                "nivel": level,
                "premio": name,
                "arquivo": str(destination.relative_to(OUT)),
                "status": "reutilizada",
            })
            manifest.append(record)
            continue

        errors = []
        chosen = None
        image = None
        candidates, search_errors = collect_candidates(name)
        errors.extend(search_errors)
        ranked = sorted(
            candidates,
            key=lambda item: relevance_score(name, item.get("title", "")),
            reverse=True,
        )

        threshold = minimum_score(name)
        for candidate in ranked[:15]:
            score = relevance_score(name, candidate.get("title", ""))
            if score < threshold:
                break
            try:
                image = download_image(candidate)
                chosen = dict(candidate)
                chosen["score"] = score
                break
            except Exception as exc:
                errors.append(f"download {candidate.get('marketplace')}: {exc}")

        if chosen and image:
            fit_web(image).save(destination, "WEBP", quality=88, method=6)
            status = "ok"
            marketplace = chosen.get("marketplace", "")
            listing_title = chosen.get("title", "")
            listing_url = chosen.get("listing_url", "")
            image_url = chosen.get("image_url", "")
            score = round(float(chosen.get("score", 0)), 4)
        else:
            make_missing(name, box).save(destination, "WEBP", quality=88, method=6)
            status = "sem-correspondencia"
            marketplace = ""
            listing_title = ""
            listing_url = ""
            image_url = ""
            score = 0.0

        record = {
            "id": product_id,
            "caixa": box,
            "nivel": level,
            "premio": name,
            "arquivo": str(destination.relative_to(OUT)),
            "status": status,
            "marketplace": marketplace,
            "titulo_anuncio": listing_title,
            "url_anuncio": listing_url,
            "url_imagem": image_url,
            "score_correspondencia": score,
            "erros": " | ".join(errors)[:1200],
        }
        manifest.append(record)
        cache[cache_key] = {"path": destination, "record": record}
        time.sleep(0.12)

    fields = list(manifest[0].keys())
    with open(OUT / "manifest.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest)
    with open(OUT / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    summary = {
        "total": len(manifest),
        "ok": sum(1 for item in manifest if item["status"] in {"ok", "reutilizada"}),
        "sem_correspondencia": sum(1 for item in manifest if item["status"] == "sem-correspondencia"),
        "mercado_livre": sum(1 for item in manifest if item["marketplace"] == "Mercado Livre"),
        "shopee": sum(1 for item in manifest if item["marketplace"] == "Shopee"),
    }
    (OUT / "resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "LEIA-ME.txt").write_text(
        "PACOTE DE IMAGENS DE PRODUTOS\n\n"
        "As imagens deste pacote foram baixadas exclusivamente de anúncios reais do Mercado Livre e da Shopee.\n"
        "O manifest.csv informa a origem, o título do anúncio e o score de correspondência.\n"
        "Entradas sem correspondência confiável foram marcadas com um placeholder, sem usar imagens aleatórias.\n"
        "As imagens pertencem aos respectivos vendedores ou titulares e devem ser revisadas antes do uso comercial.\n",
        encoding="utf-8",
    )

    zip_path = BASE / "product-images-marketplaces.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in OUT.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(OUT))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"ZIP: {zip_path}", flush=True)


if __name__ == "__main__":
    main()
