'use client';
import { useState } from 'react';
import './styles.css';
const API = process.env.NEXT_PUBLIC_LEADS_API_URL || '';

type Prospect = {
  name: string; video_title: string; video_url: string; channel_url: string;
  subs: number; email?: string; instagram?: string; last_video_at?: string; query_source: string;
};

export default function Page(){
  const [queries, setQueries] = useState('r&b official audio\nalt r&b official audio\nafro r&b official audio');
  const [days, setDays] = useState(120);
  const [minSubs, setMinSubs] = useState(1000);
  const [maxSubs, setMaxSubs] = useState(250000);
  const [rows, setRows] = useState<Prospect[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Prospect|null>(null);
  const [channel, setChannel] = useState<'email'|'ig'>('email');
  const [subject, setSubject] = useState('Beats for your next drop – quick pack');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);

  async function run(){
    if(!API){ alert('Set NEXT_PUBLIC_LEADS_API_URL'); return; }
    setLoading(true);
    const body = { queries: queries.split('\n').map(s=>s.trim()).filter(Boolean), days_back: days, min_subs: minSubs, max_subs: maxSubs, max_results_per_query: 60 };
    const r = await fetch(`${API.replace(/\/$/,'')}/search`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const data = await r.json(); setRows(data); setLoading(false);
  }

  async function compose(p: Prospect, ch: 'email'|'ig'){
    setSelected(p); setChannel(ch);
    const body = { name: p.name, video_title: p.video_title, channel_url: p.channel_url, channel: ch, include_demo_master: true };
    const r = await fetch(`${API.replace(/\/$/,'')}/compose`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const d = await r.json(); setMessage(d.message);
    if(ch==='email') setSubject(`Beats for ${p.name} – quick pack`);
  }

  async function sendEmail(p: Prospect){
    if(!p.email){ alert('No email for this prospect'); return; }
    setSending(true);
    const r = await fetch(`${API.replace(/\/$/,'')}/send-email`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ to_email: p.email, subject, body: message }) });
    setSending(false);
    if(r.ok){ alert('Sent'); } else { const t = await r.text(); alert('Send failed: ' + t); }
  }

  return (
    <div className="container">
      <h1>Leads + Outreach</h1>
      <div className="card">
        <div className="small">YouTube Data API key must be set on the API. Email sending requires SendGrid env vars.</div>
        <textarea className="input" rows={4} value={queries} onChange={e=>setQueries(e.target.value)} />
        <div style={{display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10, marginTop:10}}>
          <input className="input" type="number" value={days} onChange={e=>setDays(Number(e.target.value))} />
          <input className="input" type="number" value={minSubs} onChange={e=>setMinSubs(Number(e.target.value))} />
          <input className="input" type="number" value={maxSubs} onChange={e=>setMaxSubs(Number(e.target.value))} />
        </div>
        <div style={{marginTop:10}} className="flex">
          <button className="btn" onClick={run} disabled={loading}>{loading?'Searching…':'Search'}</button>
          {rows.length>0 && <a className="btn" href={`${API.replace(/\/$/,'')}/export.csv`}>Export CSV</a>}
        </div>
      </div>

      {rows.length>0 && (
        <div className="card" style={{marginTop:16}}>
          <table className="table">
            <thead><tr><th>Name</th><th>Subs</th><th>Last Video</th><th>Email</th><th>IG</th><th>Links</th><th>Outreach</th></tr></thead>
            <tbody>
              {rows.map((r,i)=> (
                <tr key={i}>
                  <td>{r.name}</td>
                  <td>{r.subs}</td>
                  <td className="small">{r.last_video_at?.slice(0,10)}</td>
                  <td className="small">{r.email||''}</td>
                  <td className="small">{r.instagram||''}</td>
                  <td className="small"><a href={r.video_url} target="_blank">video</a> · <a href={r.channel_url} target="_blank">channel</a></td>
                  <td className="small">
                    <button className="btn" onClick={()=>compose(r,'email')} disabled={!r.email}>Email</button>
                    <button className="btn" onClick={()=>compose(r,'ig')} style={{marginLeft:6}}>IG DM</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <div className="card" style={{marginTop:16}}>
          <div className="flex"><div><b>Selected:</b> {selected.name}</div><div className="small">Channel: {channel}</div></div>
          {channel==='email' && (<input className="input" style={{marginTop:8}} value={subject} onChange={e=>setSubject(e.target.value)} />)}
          <textarea className="input" style={{marginTop:8}} value={message} onChange={e=>setMessage(e.target.value)} />
          <div className="flex" style={{marginTop:8}}>
            {channel==='email' ? (
              <button className="btn" onClick={()=>sendEmail(selected)} disabled={sending || !selected.email}>{sending?'Sending…':'Send Email'}</button>
            ) : (
              <a className="btn" href={selected.instagram || selected.channel_url} target="_blank">Open Profile (copy text)</a>
            )}
            <button className="btn" onClick={()=>{navigator.clipboard.writeText(message);}} style={{marginLeft:6}}>Copy Message</button>
          </div>
        </div>
      )}
    </div>
  );
}
