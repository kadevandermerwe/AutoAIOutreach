from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os, re, datetime, httpx, csv, io, time, random
import sqlalchemy as sa
from sqlalchemy import text

# Optional deps
SENDGRID_AVAILABLE = True
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SENDGRID_AVAILABLE = False

OPENAI_AVAILABLE = True
try:
    import openai
except Exception:
    OPENAI_AVAILABLE = False

# --- artist-focused filters (add these) ---
EXCLUDE_VIDEO = re.compile(
    r"(?i)\b(type\s*beat|instrumental|mix|playlist|full\s*album|dj\s*mix|karaoke|cover|tribute|sped\s*up|slowed|8d|remaster|remastered|199\d|200\d)\b"
)
EXCLUDE_CHANNEL = re.compile(
    r"(?i)\b(beats?|type\s*beats?|instrumentals?|producer|prod\.?|beatmaker|records|mixtapes?)\b"
)
INCLUDE_VIDEO = re.compile(
    r"(?i)\b(official (audio|video)|visualizer|lyric video|single|performance)\b"
)
EXCLUDE_HANDLE = re.compile(r"(?i)(beats|prod|producer)")

# NEW: fan/reupload/fancam filters
EXCLUDE_REPOST = re.compile(
    r"(?i)\b(fan[\s-]?cam|fan[\s-]?made|fan[\s-]?edit|edit|re[-\s]?upload|"
    r"no\s+copyright|i\s+do\s+not\s+own\s+the\s+rights|credits?\s+to)\b"
)

# NEW: block huge/fandom keywords that skew results toward big acts
EXCLUDE_BIG_ARTISTS = re.compile(
    r"(?i)\b(blackpink|bts|stray\s*kids|twice|seventeen|nct|taylor\s*swift|"
    r"billie\s*eilish|olivia\s*rodrigo|ariana\s*grande|drake|bad\s*bunny|doja\s*cat|eminem|rihanna|dua\s*lipa)\b"
)

# Skip auto-generated “- Topic” channels
EXCLUDE_TOPIC_CH = re.compile(r"(?i)\b\-?\s*topic\b")


DATABASE_URL = os.getenv('DATABASE_URL')
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS','*')
YT_API_KEY = os.getenv('YT_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if OPENAI_AVAILABLE and OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

EMAIL_FROM = os.getenv('EMAIL_FROM','beats@yourdomain.com')
EMAIL_FROM_NAME = os.getenv('EMAIL_FROM_NAME','Kade')
EMAIL_RATE_SECONDS = int(os.getenv('EMAIL_RATE_SECONDS','45'))
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')

app = FastAPI(title='Leads API', version='2.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'] if ALLOWED_ORIGINS=='*' else ALLOWED_ORIGINS.split(','),
    allow_credentials=True, allow_methods=['*'], allow_headers=['*']
)
engine = sa.create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

with engine.begin() as cx:
    cx.exec_driver_sql('''
    CREATE TABLE IF NOT EXISTS prospects (
      id TEXT PRIMARY KEY,
      name TEXT, platform TEXT, handle TEXT,
      email TEXT, instagram TEXT,
      subs INTEGER, last_video_at TIMESTAMP, video_title TEXT,
      video_url TEXT, channel_url TEXT, query_source TEXT,
      created_at TIMESTAMP NOT NULL
    );''')
    cx.exec_driver_sql('''
    CREATE TABLE IF NOT EXISTS outbox (
      id TEXT PRIMARY KEY,
      prospect_id TEXT,
      channel TEXT,
      to_addr TEXT,
      body TEXT,
      status TEXT,
      error TEXT,
      created_at TIMESTAMP NOT NULL,
      sent_at TIMESTAMP
    );''')


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+")

class SearchRequest(BaseModel):
    queries: List[str]
    days_back: int = 90
    min_subs: int = 1000
    max_subs: int = 120000  # tighter cap to avoid big-label gravity
    max_results_per_query: int = 60
    strict_artist_filter: bool = True
    min_video_views: int = 300
    exclude_keywords: List[str] = []  # runtime blocklist


class Prospect(BaseModel):
    name: str
    video_title: str
    video_url: str
    channel_url: str
    subs: int
    email: Optional[str]
    instagram: Optional[str]
    last_video_at: Optional[str]
    query_source: str

class ComposeRequest(BaseModel):
    name: str
    video_title: Optional[str] = None
    channel_url: Optional[str] = None
    lane: str = "warm, sparse Alt-R&B (Brent/Majid pocket)"
    offer: str = "5-hook pack (25–40s, tagged) + customs from $400 (50% to book, 2 revs)"
    include_demo_master: bool = True
    channel: str = "email"   # 'email' or 'ig'

class ComposeResponse(BaseModel):
    message: str

class SendEmailRequest(BaseModel):
    prospect_id: Optional[str] = None
    to_email: str
    subject: str
    body: str

class OutboxItem(BaseModel):
    id: str
    status: str

def _compose_template(req: ComposeRequest)->str:
    headline = f"Yo {req.name}," if req.channel=='email' else f"Yo {req.name.split(' ')[0]},"
    ref = f" heard “{req.video_title}”" if req.video_title else ""
    dm = f"I build {req.lane}. {('I include a free Demo Master so you can hear it release-loud.' if req.include_demo_master else '')}"
    offer = f"Here’s a {req.offer}. If one hits, we can lock it fast."
    cta = "Want me to send the pack or build around your vocal?"
    return f"""{headline} {ref} the pocket is clean.

{dm}
{offer}

— Kade
"""

@app.post('/compose', response_model=ComposeResponse)
async def compose(req: ComposeRequest):
    # LLM if available, else template
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            prompt = f"""
Write a 70–100 word outgoing message from a producer (Kade) to an artist named {req.name}. Tone: concise, friendly, zero fluff, no emojis. Lane: {req.lane}. Reference title: {req.video_title}. Offer: {req.offer}. {"Include a 'Demo Master' note." if req.include_demo_master else ""} Channel: {req.channel}. Do not add links. End with a clear yes/no question.
"""
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"system","content":"You write concise music outreach messages that never overpromise."},
                          {"role":"user","content": prompt}],
                temperature=0.6,
                max_tokens=180
            )
            msg = completion.choices[0].message["content"].strip()
            return ComposeResponse(message=msg)
        except Exception as e:
            pass
    return ComposeResponse(message=_compose_template(req))

@app.get('/prospects', response_model=List[Prospect])
async def list_prospects(limit: int = 200):
    with engine.begin() as cx:
        rows = cx.execute(text('SELECT name, video_title, video_url, channel_url, subs, email, instagram, last_video_at, query_source FROM prospects ORDER BY last_video_at DESC LIMIT :lim'), dict(lim=limit)).all()
    return [Prospect(**dict(r)) for r in rows]

@app.post('/send-email', response_model=OutboxItem)
async def send_email(req: SendEmailRequest):
    if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY:
        raise HTTPException(400, 'Email sending not configured (SendGrid)')
    to_email = req.to_email.strip()
    if not to_email or '@' not in to_email:
        raise HTTPException(400, 'Invalid email')
    # naive rate limit: check last sent
    now = datetime.datetime.utcnow()
    with engine.begin() as cx:
        last = cx.execute(text('SELECT sent_at FROM outbox WHERE status=:st ORDER BY sent_at DESC LIMIT 1'), dict(st='sent')).first()
    if last and last[0]:
        delta = (now - last[0]).total_seconds()
        if delta < EMAIL_RATE_SECONDS:
            import time, random
            time.sleep(EMAIL_RATE_SECONDS - delta + random.uniform(0,3))
    # send
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    mail = Mail(
        from_email=(EMAIL_FROM, EMAIL_FROM_NAME),
        to_emails=[to_email],
        subject=req.subject.strip(),
        plain_text_content=req.body.strip(),
    )
    oid = f"email_{int(time.time()*1000)}"
    with engine.begin() as cx:
        cx.execute(text('INSERT INTO outbox (id, prospect_id, channel, to_addr, body, status, created_at) VALUES (:id,:pid,:ch,:to,:body,:st,:ts)'),
                   dict(id=oid, pid=req.prospect_id, ch='email', to=to_email, body=req.body, st='sending', ts=now))
    try:
        resp = sg.send(mail)
        ok = 200 <= resp.status_code < 300
        with engine.begin() as cx:
          cx.execute(text('UPDATE outbox SET status=:st, sent_at=:ts, error=:er WHERE id=:id'),
                     dict(st='sent' if ok else 'error', ts=datetime.datetime.utcnow(), er=None if ok else f"status {resp.status_code}", id=oid))
        if not ok:
            raise HTTPException(500, f"SendGrid status {resp.status_code}")
    except Exception as e:
        with engine.begin() as cx:
            cx.execute(text('UPDATE outbox SET status=:st, sent_at=:ts, error=:er WHERE id=:id'),
                       dict(st='error', ts=datetime.datetime.utcnow(), er=str(e), id=oid))
        raise HTTPException(500, f"Email send failed: {e}")
    return OutboxItem(id=oid, status='sent')

@app.post('/search', response_model=List[Prospect])
async def search(req: SearchRequest):
    if not YT_API_KEY:
        raise HTTPException(400, 'YT_API_KEY not set')
    published_after = (datetime.datetime.utcnow() - datetime.timedelta(days=req.days_back)).isoformat('T') + 'Z'
    out = []
    async with httpx.AsyncClient(timeout=20) as http:
        for q in req.queries:
            page_token = None
            fetched = 0
            while fetched < req.max_results_per_query:
                params = dict(part='id,snippet', q=q, type='video', order='date', maxResults=50, publishedAfter=published_after, key=YT_API_KEY, videoDuration='medium')


                if page_token: params['pageToken'] = page_token
                r = await http.get('https://www.googleapis.com/youtube/v3/search', params=params)
                r.raise_for_status()
                data = r.json()
                items = data.get('items', [])
                if not items: break
                video_ids = [it['id']['videoId'] for it in items if it['id'].get('videoId')]
                if not video_ids: break
                vr = await http.get('https://www.googleapis.com/youtube/v3/videos', params=dict(part='snippet,statistics', id=','.join(video_ids), key=YT_API_KEY))
                vr.raise_for_status()
                vitems = {it['id']: it for it in vr.json().get('items', [])}
                channel_ids = list({ it['snippet']['channelId'] for it in vitems.values() })
                if channel_ids:
                    cr = await http.get('https://www.googleapis.com/youtube/v3/channels', params=dict(part='snippet,statistics,brandingSettings', id=','.join(channel_ids), key=YT_API_KEY))
                    cr.raise_for_status()
                    citems = {it['id']: it for it in cr.json().get('items', [])}
                else:
                    citems = {}

                for vid in video_ids:
                    v = vitems.get(vid)
                    if not v: continue
                    ch = citems.get(v['snippet']['channelId'])
                    if not ch: continue
                    subs = int(ch.get('statistics',{}).get('subscriberCount', 0))
                    v_snip = v['snippet']
                    v_title = v_snip['title']
                    v_desc  = v_snip.get('description', '') or ''
                    ch_title = ch['snippet']['title']
                    ch_desc  = (ch['snippet'].get('description','') + '\n' +
                                ch.get('brandingSettings',{}).get('channel',{}).get('description',''))
                    handle   = ch['snippet'].get('customUrl') or ''
                    subs     = int(ch.get('statistics',{}).get('subscriberCount', 0))
                    views    = int(v.get('statistics',{}).get('viewCount', 0))
                    live     = v_snip.get('liveBroadcastContent','none')
                    published_at = v_snip['publishedAt']
                    video_url    = f"https://www.youtube.com/watch?v={vid}"
                    channel_url  = f"https://www.youtube.com/channel/{v_snip['channelId']}"
                    
                    # subscriber bounds (tighter)
                    if subs < req.min_subs or subs > req.max_subs:
                        continue
                    
                    # skip live streams/premieres
                    if live and live.lower() != 'none':
                        continue
                    
                    # block obvious repost/fandom/big-artist gravity
                    if EXCLUDE_REPOST.search(v_title) or EXCLUDE_REPOST.search(v_desc) or EXCLUDE_REPOST.search(ch_desc):
                        continue
                    if EXCLUDE_BIG_ARTISTS.search(v_title) or EXCLUDE_BIG_ARTISTS.search(ch_title):
                        continue
                    
                    # skip auto-generated “- Topic” channels
                    if EXCLUDE_TOPIC_CH.search(ch_title):
                        continue
                    
                    # skip producer/labelish channels/handles
                    if EXCLUDE_CHANNEL.search(ch_title) or EXCLUDE_CHANNEL.search(ch_desc):
                        continue
                    if EXCLUDE_HANDLE.search(handle):
                        continue
                    
                    # skip beat/instrumental uploads
                    if EXCLUDE_VIDEO.search(v_title):
                        continue
                    
                    # prefer artist-y signals
                    looks_official = bool(INCLUDE_VIDEO.search(v_title))
                    looks_artist_bio = bool(re.search(r"(?i)\b(artist|singer|songwriter|musician|official)\b", ch_desc))
                    cat = v_snip.get('categoryId')
                    if cat and str(cat) != '10':  # Music category
                        continue
                    if req.strict_artist_filter and not (looks_official or looks_artist_bio):
                        continue
                    
                    # quality floor
                    if views < req.min_video_views:
                        continue
                    
                    # runtime keyword blocklist (from request)
                    if req.exclude_keywords:
                        joined = f"{v_title}\n{v_desc}\n{ch_title}\n{handle}"
                        if any(kw.lower() in joined.lower() for kw in req.exclude_keywords):
                            continue
                    
                    # dedupe by channel_url
                        # dedupe by channel_url and rank by score then recency
                    seen = set(); deduped = []
                    for r in sorted(out, key=lambda x: (x.get('_score', 0), x['last_video_at']), reverse=True):
                        key = r['channel_url']
                        if key in seen:
                            continue
                        seen.add(key)
                        r.pop('_score', None)  # remove transient
                        deduped.append(r)
                
                    # store
                    with engine.begin() as cx:
                        for r in deduped:
                            cx.execute(text('DELETE FROM prospects WHERE id=:id'), dict(id=r['id']))
                            cols = ','.join(r.keys()); vals = ','.join([f":{k}" for k in r.keys()])
                            cx.execute(text(f'INSERT INTO prospects ({cols}) VALUES ({vals})'), r)
                
                    # >>> DO NOT FORGET TO RETURN A LIST (even if empty)
                    if not deduped:
                        return []
                
                    return [
                        Prospect(
                            name=r['name'],
                            video_title=r['video_title'],
                            video_url=r['video_url'],
                            channel_url=r['channel_url'],
                            subs=r['subs'],
                            email=r.get('email'),
                            instagram=r.get('instagram'),
                            last_video_at=r.get('last_video_at'),
                            query_source=r['query_source'],
                        )
                        for r in deduped
                    ]


@app.get('/export.csv')
async def export_csv():
    with engine.begin() as cx:
        rows = cx.execute(text('SELECT name, video_title, video_url, channel_url, subs, email, instagram, last_video_at, query_source FROM prospects ORDER BY last_video_at DESC')).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['name','video_title','video_url','channel_url','subs','email','instagram','last_video_at','query_source'])
    for r in rows:
        w.writerow(list(r))
    from fastapi.responses import StreamingResponse
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type='text/csv', headers={'Content-Disposition':'attachment; filename=leads.csv'})
