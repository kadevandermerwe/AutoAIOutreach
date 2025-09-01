from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, uuid, shutil, json, subprocess, datetime
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Engine

DATABASE_URL = os.getenv('DATABASE_URL')
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS','*')
MASTERING_CLI_PATH = os.getenv('MASTERING_CLI_PATH','')

app = FastAPI(title='Mastering API', version='1.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'] if ALLOWED_ORIGINS=='*' else ALLOWED_ORIGINS.split(','),
    allow_credentials=True, allow_methods=['*'], allow_headers=['*']
)

engine: Engine = sa.create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

with engine.begin() as cx:
    cx.exec_driver_sql('''
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL,
        finished_at TIMESTAMP,
        preset TEXT,
        target_lufs REAL,
        true_peak REAL,
        input_path TEXT,
        output_path TEXT,
        metrics_json TEXT
    );
    ''')

class CreateJobResponse(BaseModel):
    id: str
    status: str

class JobResponse(BaseModel):
    id: str
    status: str
    created_at: str
    finished_at: Optional[str] = None
    preset: Optional[str] = None
    target_lufs: Optional[float] = None
    true_peak: Optional[float] = None
    output_url: Optional[str] = None
    metrics: Optional[dict] = None

DATA_DIR = '/opt/data'
os.makedirs(DATA_DIR, exist_ok=True)

@app.post('/v1/jobs', response_model=CreateJobResponse)
async def create_job(background: BackgroundTasks,
                     file: UploadFile = File(...),
                     preset: str = Form('streaming'),
                     target_lufs: float = Form(-14.0),
                     true_peak: float = Form(-1.0)):
    jid = str(uuid.uuid4())
    job_dir = os.path.join(DATA_DIR, jid)
    os.makedirs(job_dir, exist_ok=True)
    in_path = os.path.join(job_dir, 'input.wav')
    out_path = os.path.join(job_dir, 'output_master.wav')
    with open(in_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)

    with engine.begin() as cx:
        cx.execute(text('INSERT INTO jobs (id,status,created_at,preset,target_lufs,true_peak,input_path,output_path)\
                  VALUES (:id,:st,:ts,:pr,:lu,:tp,:inp,:outp)'),
           dict(id=jid, st='queued', ts=datetime.datetime.utcnow(), pr=preset,
                lu=target_lufs, tp=true_peak, inp=in_path, outp=out_path))

    background.add_task(_process_job, jid)
    return CreateJobResponse(id=jid, status='queued')

@app.get('/v1/jobs/{jid}', response_model=JobResponse)
async def get_job(jid: str):
    with engine.begin() as cx:
        row = cx.execute(text('SELECT * FROM jobs WHERE id=:id'), dict(id=jid)).mappings().first()
    if not row:
        raise HTTPException(404, 'job not found')
    output_url = None
    if row['status'] == 'done':
        output_url = f"/v1/jobs/{jid}/result"
    metrics = json.loads(row['metrics_json']) if row['metrics_json'] else None
    return JobResponse(
        id=row['id'], status=row['status'], created_at=row['created_at'].isoformat()+'Z',
        finished_at=row['finished_at'].isoformat()+'Z' if row['finished_at'] else None,
        preset=row['preset'], target_lufs=row['target_lufs'], true_peak=row['true_peak'],
        output_url=output_url, metrics=metrics
    )

@app.get('/v1/jobs/{jid}/result')
async def download_result(jid: str):
    with engine.begin() as cx:
        row = cx.execute(text('SELECT output_path,status FROM jobs WHERE id=:id'), dict(id=jid)).first()
    if not row or row[1] != 'done':
        raise HTTPException(404, 'not ready')
    path = row[0]
    if not os.path.exists(path):
        raise HTTPException(404, 'file missing')
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type='audio/wav', filename='master.wav')

def _process_job(jid: str):
    with engine.begin() as cx:
        row = cx.execute(text('SELECT * FROM jobs WHERE id=:id'), dict(id=jid)).mappings().first()
    if not row:
        return
    in_path, out_path = row['input_path'], row['output_path']

    with engine.begin() as cx:
        cx.execute(text('UPDATE jobs SET status=:st WHERE id=:id'), dict(st='running', id=jid))

    try:
        if MASTERING_CLI_PATH:
            cmd = [MASTERING_CLI_PATH, in_path, out_path, '--lufs', str(row['target_lufs']), '--tp', str(row['true_peak']), '--preset', row['preset'] or 'streaming']
            subprocess.run(cmd, check=True)
        else:
            shutil.copyfile(in_path, out_path)
        metrics = { 'lufs': row['target_lufs'], 'true_peak': row['true_peak'], 'preset': row['preset'] }
        with engine.begin() as cx:
            cx.execute(text('UPDATE jobs SET status=:st, finished_at=:ft, metrics_json=:mj WHERE id=:id'),
                       dict(st='done', ft=datetime.datetime.utcnow(), mj=json.dumps(metrics), id=jid))
    except Exception as e:
        with engine.begin() as cx:
            cx.execute(text('UPDATE jobs SET status=:st, finished_at=:ft, metrics_json=:mj WHERE id=:id'),
                       dict(st='error', ft=datetime.datetime.utcnow(), mj=json.dumps({'error': str(e)}), id=jid))
