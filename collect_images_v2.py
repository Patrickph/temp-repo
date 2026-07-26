import csv, io, os, re, json, shutil, time, zipfile, unicodedata, html
from pathlib import Path
from urllib.parse import quote_plus
import requests
from PIL import Image, ImageOps, ImageDraw

BASE=Path(__file__).resolve().parent
OUT=BASE/'output'
OUT.mkdir(exist_ok=True)
S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36'})

HINTS={
'mini impressora termica':'mini thermal printer product white background','mini seladora':'mini bag sealer product white background','carregador magnetico':'magnetic wireless charger product white background','fone bluetooth':'wireless earbuds product white background','luminaria sunset':'sunset lamp product','copo termico':'insulated tumbler product white background','organizador de cabos':'cable organizer product white background','mini aspirador':'mini handheld vacuum product white background','liquidificador portatil':'portable blender product white background','caixa de som bluetooth':'bluetooth speaker product white background','projetor portatil':'portable projector product white background','extratora':'upholstery cleaner product white background','robo aspirador':'robot vacuum product white background','escova secadora':'hot air brush product white background','ferramentas sem fio':'cordless power tool kit product white background','camisa retro':'retro football jersey product','cachecol':'football scarf product','chuteira':'soccer cleats product white background','luva de goleiro':'goalkeeper gloves product white background','gift card steam':'Steam gift card','gift card google play':'Google Play gift card','gift card robux':'Roblox gift card','gift card apple':'Apple gift card','gift card playstation':'PlayStation gift card','gift card xbox':'Xbox gift card','gift card nintendo':'Nintendo gift card','steam deck oled':'Steam Deck OLED product','rog ally':'ROG Ally product','apple airtag':'Apple AirTag product','apple airpods':'Apple AirPods product','apple watch':'Apple Watch product','apple pencil':'Apple Pencil product','apple ipad':'Apple iPad product','magic mouse':'Apple Magic Mouse product','magic keyboard':'Apple Magic Keyboard product','iphone pro':'iPhone Pro product','macbook air':'MacBook Air product','macbook pro':'MacBook Pro product','ipad pro':'iPad Pro product','airpods max':'AirPods Max product','scanner obd2':'OBD2 scanner product','dashcam':'dash camera product','ring light':'ring light product','selfie stick':'selfie stick product','gimbal':'smartphone gimbal product','dji osmo pocket':'DJI Osmo Pocket product','sony zv-e10 ii':'Sony ZV-E10 II product','canon powershot v1':'Canon PowerShot V1 product','insta360 x5':'Insta360 X5 product'}

def norm(s):
 s=unicodedata.normalize('NFKD',s); s=''.join(c for c in s if not unicodedata.combining(c)); return re.sub(r'\s+',' ',s.lower()).strip()
def slug(s): return re.sub(r'[^a-z0-9]+','-',norm(s)).strip('-')[:90] or 'produto'
def query_for(name):
 n=norm(name)
 for k,v in HINTS.items():
  if k in n:return v
 return f'{name} produto foto fundo branco'

def bing_images(q):
 url='https://www.bing.com/images/search?q='+quote_plus(q)+'&form=HDRSC2&first=1'
 r=S.get(url,timeout=30); r.raise_for_status(); t=r.text
 urls=[]
 patterns=[r'murl&quot;:&quot;(.*?)&quot;',r'"murl":"(.*?)"',r'mediaurl=(https?%3A%2F%2F[^&]+)']
 for p in patterns:
  for u in re.findall(p,t):
   u=html.unescape(u).replace('\\/','/')
   if u.startswith('http') and u not in urls: urls.append(u)
 return [{'url':u,'source':'Bing Images','page':url} for u in urls[:30]]

def openverse(q):
 r=S.get('https://api.openverse.org/v1/images/',params={'q':q,'page_size':20,'mature':'false'},timeout=30); r.raise_for_status()
 out=[]
 for x in r.json().get('results',[]):
  u=x.get('thumbnail') or x.get('url')
  if u: out.append({'url':u,'source':'Openverse','page':x.get('foreign_landing_url',''),'creator':x.get('creator',''),'license':x.get('license','')})
 return out

def download(u):
 r=S.get(u,timeout=35); r.raise_for_status()
 if len(r.content)<2500: raise ValueError('arquivo pequeno')
 im=Image.open(io.BytesIO(r.content)); im.load(); im=im.convert('RGB')
 if min(im.size)<220: raise ValueError('resolucao baixa')
 return im

def fit(im):
 c=Image.new('RGB',(700,700),'white'); f=ImageOps.contain(im,(650,650),Image.Resampling.LANCZOS); c.paste(f,((700-f.width)//2,(700-f.height)//2)); return c

def placeholder(name,box):
 im=Image.new('RGB',(700,700),'white'); d=ImageDraw.Draw(im); d.rectangle((20,20,680,680),outline='#cbd5e1',width=4); d.text((50,60),box,fill='#64748b'); d.text((50,320),name[:55],fill='#111827'); d.text((50,620),'sem imagem encontrada',fill='#dc2626'); return im

def main():
 shutil.rmtree(OUT,ignore_errors=True); OUT.mkdir()
 rows=list(csv.DictReader(open(BASE/'products.csv',encoding='utf-8')))
 cache={}; manifest=[]
 for i,row in enumerate(rows,1):
  name=row['premio'].strip(); box=row['caixa'].strip(); q=query_for(name); key=norm(q)
  folder=OUT/slug(box); folder.mkdir(parents=True,exist_ok=True); dest=folder/f"{int(row['id']):03d}-{slug(name)}.webp"
  meta={'source':'','page':'','url':'','creator':'','license':''}; status='ok'; err=''
  print(f'[{i}/{len(rows)}] {name}',flush=True)
  try:
   if key in cache:
    shutil.copy2(cache[key]['path'],dest); meta=cache[key]['meta']; status='reutilizada'
   else:
    candidates=[]
    try:candidates+=bing_images(q)
    except Exception as e:err+=f'Bing:{e};'
    try:candidates+=openverse(q)
    except Exception as e:err+=f'Openverse:{e};'
    im=None; chosen=None
    for x in candidates[:35]:
     try: im=download(x['url']); chosen=x; break
     except Exception as e: err+=f'dl:{e};'
    if im is None: im=placeholder(name,box); status='placeholder'
    else:
     meta={'source':chosen.get('source',''),'page':chosen.get('page',''),'url':chosen.get('url',''),'creator':chosen.get('creator',''),'license':chosen.get('license','')}
    fit(im).save(dest,'WEBP',quality=88,method=6); cache[key]={'path':dest,'meta':meta}
  except Exception as e:
   placeholder(name,box).save(dest,'WEBP',quality=88); status='erro-placeholder'; err+=str(e)
  manifest.append({'id':row['id'],'caixa':box,'nivel':row['nivel'],'premio':name,'arquivo':str(dest.relative_to(OUT)),'status':status,'fonte':meta['source'],'pagina_origem':meta['page'],'url_imagem':meta['url'],'criador':meta['creator'],'licenca':meta['license'],'erro':err[:500]})
  time.sleep(.1)
 fields=list(manifest[0]);
 with open(OUT/'manifest.csv','w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest)
 (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 (OUT/'LEIA-ME.txt').write_text('Imagens representativas coletadas da internet. Consulte manifest.csv para origem. Imagens de busca podem ter direitos autorais e precisam ser revisadas antes do uso comercial.',encoding='utf-8')
 with zipfile.ZipFile(BASE/'product-images.zip','w',zipfile.ZIP_DEFLATED) as z:
  for p in OUT.rglob('*'):
   if p.is_file():z.write(p,p.relative_to(OUT))
 print('concluido',flush=True)
if __name__=='__main__':main()
