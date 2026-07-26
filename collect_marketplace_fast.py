import asyncio,csv,io,json,re,shutil,unicodedata,zipfile
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus
import requests
from PIL import Image,ImageDraw,ImageOps
from playwright.async_api import async_playwright

BASE=Path(__file__).resolve().parent; OUT=BASE/'output'
HTTP=requests.Session(); HTTP.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept-Language':'pt-BR,pt;q=0.9','Accept':'image/avif,image/webp,image/*,*/*;q=0.8'})
STOP={'de','da','do','das','dos','para','com','sem','ou','e','em','um','uma','o','a','os','as','kit','produto','modelo','atual','geracao','premium','basico','basica','grande','completo','completa','alto','alta','valor','novo','nova','reais','r'}
SYN={'smartphone':{'smartphone','celular'},'celular':{'smartphone','celular'},'notebook':{'notebook','laptop'},'tv':{'tv','televisao','smarttv'},'fone':{'fone','earbud','earbuds','headphone','headset'},'console':{'console','videogame'},'videogame':{'console','videogame'},'controle':{'controle','joystick','gamepad'},'robo':{'robo','robot'},'cartao':{'cartao','gift','card'},'gift':{'gift','card','cartao'},'card':{'gift','card','cartao'},'mousepad':{'mousepad','deskmat'}}
ALIASES={'console com ea sports fc':'console ea sports fc','pc gamer completo':'computador gamer completo','pc portatil gamer':'console portatil gamer','iphone pro da geracao atual':'iphone pro','gift card robux de alto valor':'gift card roblox','kit completo de estudio':'kit estudio fotografia video'}

def norm(s):
 s=unicodedata.normalize('NFKD',s or ''); s=''.join(c for c in s if not unicodedata.combining(c)); return re.sub(r'\s+',' ',re.sub(r'[^a-zA-Z0-9]+',' ',s.lower())).strip()
def slug(s): return re.sub(r'[^a-z0-9]+','-',norm(s)).strip('-')[:90] or 'produto'
def toks(s): return [x for x in norm(s).split() if x not in STOP and len(x)>1 and not x.isdigit()]
def query(name):
 n=norm(name)
 if n in ALIASES:return ALIASES[n]
 s=re.sub(r'R\$\s*[\d\.]+(?:,\d+)?','',name,flags=re.I); s=re.sub(r'\b(?:da geração atual|de alto valor|premium|básico|básica|profissional)\b','',s,flags=re.I); return re.sub(r'\s+',' ',s).strip()
def score(product,title):
 p=toks(product); t=set(toks(title))
 if not p or not t:return 0
 m=sum(bool(SYN.get(x,{x})&t) for x in p)
 if not m:return 0
 sc=.8*(m/len(p))+.2*SequenceMatcher(None,norm(product),norm(title)).ratio()
 protected={'apple','iphone','ipad','macbook','airpods','steam','roblox','playstation','xbox','nintendo','dji','sony','canon','insta360','bitcoin','ethereum','usdt','cartier','dyson'}
 req=set(norm(product).split())&protected
 if req and not req.issubset(set(norm(title).split())):sc-=.35
 return max(0,min(1,sc))
def threshold(p):
 n=len(toks(p)); return .56 if n<=1 else .47 if n==2 else .41

def normimg(u):
 if not u:return ''
 if u.startswith('//'):u='https:'+u
 return u.replace('http://','https://').replace('D_Q_NP_','D_NQ_NP_')

async def ml(page,q):
 try: await page.goto('https://lista.mercadolivre.com.br/'+quote_plus(q).replace('+','-'),wait_until='domcontentloaded',timeout=9000)
 except: pass
 await page.wait_for_timeout(700)
 return await page.evaluate("""()=>Array.from(document.querySelectorAll('li.ui-search-layout__item,div.ui-search-result,div.poly-card')).slice(0,18).map(c=>{const a=c.querySelector('a.poly-component__title,a.ui-search-link,a[href*=\"mercadolivre.com.br\"]');const t=c.querySelector('.poly-component__title,.ui-search-item__title,h2,h3');const i=c.querySelector('img');return{title:(t?.textContent||a?.textContent||'').trim(),listing_url:a?.href||'',image_url:i?.getAttribute('data-src')||i?.currentSrc||i?.src||''}}).filter(x=>x.title&&x.image_url)""")
async def shopee(page,q):
 try: await page.goto('https://shopee.com.br/search?keyword='+quote_plus(q),wait_until='domcontentloaded',timeout=9000)
 except: pass
 await page.wait_for_timeout(1100)
 return await page.evaluate("""()=>Array.from(document.querySelectorAll('[data-sqe=\"item\"],.shopee-search-item-result__item')).slice(0,18).map(c=>{const a=c.querySelector('a[href]');const i=c.querySelector('img');const tx=Array.from(c.querySelectorAll('div,span')).map(e=>(e.textContent||'').trim()).filter(t=>t.length>5&&t.length<220).sort((a,b)=>b.length-a.length)[0]||'';return{title:tx,listing_url:a?.href||'',image_url:i?.getAttribute('data-src')||i?.currentSrc||i?.src||''}}).filter(x=>x.title&&x.image_url)""")
def download(c):
 h=dict(HTTP.headers); h['Referer']=c.get('listing_url',''); r=HTTP.get(normimg(c['image_url']),headers=h,timeout=15); r.raise_for_status()
 if len(r.content)<2500:raise ValueError('pequena')
 im=Image.open(io.BytesIO(r.content)); im.load()
 if min(im.size)<140:raise ValueError('resolucao')
 return im.convert('RGB')
def fit(im):
 c=Image.new('RGB',(700,700),'white'); f=ImageOps.contain(im,(650,650),Image.Resampling.LANCZOS); c.paste(f,((700-f.width)//2,(700-f.height)//2)); return c
def missing(name,box):
 im=Image.new('RGB',(700,700),'#f8fafc');d=ImageDraw.Draw(im);d.rounded_rectangle((35,35,665,665),radius=28,fill='white',outline='#cbd5e1',width=5);d.text((70,90),box,fill='#64748b');d.text((70,300),name[:65],fill='#0f172a');d.text((70,575),'SEM ANUNCIO CONFIAVEL',fill='#dc2626');return im

async def main():
 if OUT.exists():shutil.rmtree(OUT)
 OUT.mkdir(); rows=list(csv.DictReader(open(BASE/'products.csv',encoding='utf-8'))); manifest=[];cache={}
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-blink-features=AutomationControlled']);ctx=await b.new_context(locale='pt-BR',viewport={'width':1366,'height':850},user_agent=HTTP.headers['User-Agent']);pages={'Mercado Livre':await ctx.new_page(),'Shopee':await ctx.new_page()}
  for ix,row in enumerate(rows,1):
   pid=int(row['id']);box=row['caixa'].strip();level=row['nivel'].strip();name=row['premio'].strip();destdir=OUT/slug(box);destdir.mkdir(parents=True,exist_ok=True);dest=destdir/f'{pid:03d}-{slug(name)}.webp';key=norm(name);print(f'[{ix}/{len(rows)}] {name}',flush=True)
   if key in cache:
    shutil.copy2(cache[key]['path'],dest);rec=dict(cache[key]['rec']);rec.update({'id':pid,'caixa':box,'nivel':level,'premio':name,'arquivo':str(dest.relative_to(OUT)),'status':'reutilizada'});manifest.append(rec);continue
   order=['Mercado Livre','Shopee'] if pid%2 else ['Shopee','Mercado Livre'];cand=[];errors=[];q=query(name)
   for src in order:
    try:
     found=await (ml(pages[src],q) if src=='Mercado Livre' else shopee(pages[src],q))
     for x in found:x['marketplace']=src
     cand+=found
     if cand and max(score(name,x['title']) for x in cand)>=threshold(name)+.08:break
    except Exception as e:errors.append(f'{src}: {e}')
   cand.sort(key=lambda x:score(name,x['title']),reverse=True);chosen=None;image=None
   for x in cand[:12]:
    sc=score(name,x['title'])
    if sc<threshold(name):break
    try:image=download(x);chosen=x;chosen['score']=sc;break
    except Exception as e:errors.append(f'download {x.get("marketplace")}: {e}')
   if chosen:
    fit(image).save(dest,'WEBP',quality=88,method=6);rec={'id':pid,'caixa':box,'nivel':level,'premio':name,'arquivo':str(dest.relative_to(OUT)),'status':'ok','marketplace':chosen['marketplace'],'titulo_anuncio':chosen['title'],'url_anuncio':chosen['listing_url'],'url_imagem':normimg(chosen['image_url']),'score_correspondencia':round(chosen['score'],4),'erros':' | '.join(errors)[:1000]}
   else:
    missing(name,box).save(dest,'WEBP',quality=88,method=6);rec={'id':pid,'caixa':box,'nivel':level,'premio':name,'arquivo':str(dest.relative_to(OUT)),'status':'sem-correspondencia','marketplace':'','titulo_anuncio':'','url_anuncio':'','url_imagem':'','score_correspondencia':0,'erros':' | '.join(errors)[:1000]}
   manifest.append(rec);cache[key]={'path':dest,'rec':rec}
  await b.close()
 fields=list(manifest[0]);f=open(OUT/'manifest.csv','w',newline='',encoding='utf-8-sig');w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest);f.close();(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');summary={'total':len(manifest),'ok':sum(x['status'] in {'ok','reutilizada'} for x in manifest),'sem_correspondencia':sum(x['status']=='sem-correspondencia' for x in manifest),'mercado_livre':sum(x['marketplace']=='Mercado Livre' for x in manifest),'shopee':sum(x['marketplace']=='Shopee' for x in manifest)};(OUT/'resumo.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');(OUT/'LEIA-ME.txt').write_text('Imagens de anuncios reais da Shopee e Mercado Livre. Verifique manifest.csv antes de publicar.\n',encoding='utf-8');zpath=BASE/'product-images-marketplaces.zip';zpath.unlink(missing_ok=True)
 with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as z:
  for path in OUT.rglob('*'):
   if path.is_file():z.write(path,path.relative_to(OUT))
 print(json.dumps(summary),flush=True)
if __name__=='__main__':asyncio.run(main())
