'use client';
import { useState } from 'react';
import './styles.css';
const API = process.env.NEXT_PUBLIC_MASTERING_API_URL || '';
export default function Page(){
  const [file, setFile] = useState<File|null>(null);
  const [preset, setPreset] = useState('streaming');
  const [lufs, setLufs] = useState(-14);
  const [tp, setTp] = useState(-1);
  const [job, setJob] = useState<any>(null);
  const [status, setStatus] = useState('idle');
  async function submit(){
    if(!file || !API){ alert('Select a file and set NEXT_PUBLIC_MASTERING_API_URL'); return; }
    const fd = new FormData();
    fd.append('file', file); fd.append('preset', preset);
    fd.append('target_lufs', String(lufs)); fd.append('true_peak', String(tp));
    setStatus('uploading');
    const resp = await fetch(`${API.replace(/\/$/,'')}/v1/jobs`, { method:'POST', body: fd });
    const data = await resp.json(); setJob({ id: data.id, status: data.status }); setStatus('queued'); poll(data.id);
  }
  async function poll(id: string){
    const i = setInterval(async ()=>{
      const r = await fetch(`${API.replace(/\/$/,'')}/v1/jobs/${id}`);
      const j = await r.json(); setJob(j); setStatus(j.status);
      if(j.status==='done' || j.status==='error') clearInterval(i);
    }, 1500);
  }
  return (<div className="container"><h1>Mastering</h1><div className="card">
    <div className="row">
      <input className="input" type="file" accept="audio/*" onChange={e=>setFile(e.target.files?.[0]||null)} />
      <div className="row">
        <select className="input" value={preset} onChange={e=>setPreset(e.target.value)}>
          <option value="streaming">Streaming</option><option value="youtube">YouTube</option><option value="club">Club</option>
        </select>
        <input className="input" type="number" step="0.1" value={lufs} onChange={e=>setLufs(Number(e.target.value))} />
        <input className="input" type="number" step="0.1" value={tp} onChange={e=>setTp(Number(e.target.value))} />
      </div>
    </div>
    <div style={{marginTop:12, display:'flex', gap:10}}><button className="btn" onClick={submit}>Create Master</button></div>
    {job && (<div style={{marginTop:20}}><div>Status: <b>{status}</b></div>
      {job.metrics && <div className="small">LUFS {job.metrics.lufs} • TP {job.metrics.true_peak} • Preset {job.metrics.preset}</div>}
      {job.output_url && <a className="btn" style={{display:'inline-block', marginTop:10}} href={`${API.replace(/\/$/,'')}${job.output_url}`}>Download master</a>}
    </div>)}
  </div></div>);
}
