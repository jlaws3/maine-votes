#!/usr/bin/env python3
"""Rebuild 2026 Maine Votes observations from a local SOS pipe-delimited report.
Never commit the source report: it contains voter record identifiers.
"""
import argparse,csv,datetime as dt,json,collections,pathlib,sys
FIELDS=['RES MUNICIPALITY','Voter Record #','P','SS','SR','CG','Req Date','Rec Date','Rec Type','Status','DUP']
ACCEPT={'ACT'}
def date(s):
    if not s:return ''
    try:return dt.datetime.strptime(s.strip(),'%m/%d/%Y').date().isoformat()
    except ValueError:return ''
def build(src):
    voters=collections.defaultdict(list); statuses=collections.Counter(); source_rows=skipped=0
    with open(src,encoding='utf-8-sig',errors='replace',newline='') as f:
        reader=csv.DictReader(f,delimiter='|')
        missing=set(FIELDS)-set(reader.fieldnames or [])
        if missing:raise ValueError(f'Missing columns: {sorted(missing)}')
        for row in reader:
            if not (row.get('Voter Record #') or '').strip():continue
            source_rows+=1
            if not (row.get('RES MUNICIPALITY') or '').strip():skipped+=1;continue
            statuses[(row.get('Status') or '').strip().upper()]+=1
            voters[row['Voter Record #'].strip()].append(row)
    obs=collections.Counter(); accepted_unique=0
    for voter,rows in voters.items():
        # One request per voter, prioritizing accepted record, then latest duplicate number.
        def priority(r):
            s=(r.get('Status') or '').strip().upper()
            dup=(r.get('DUP') or '').strip()
            return (s in ACCEPT, int(dup) if dup.isdigit() else 0)
        r=max(rows,key=priority)
        town=(r['RES MUNICIPALITY'] or '').strip().upper()
        party=(r['P'] or '').strip().upper()
        ss=(r['SS'] or '').strip();sr=(r['SR'] or '').strip();cg=(r['CG'] or '').strip()
        # Preserve the earliest request date across all records for this voter.
        req=min((d for d in (date(x.get('Req Date','')) for x in rows) if d),default='')
        if req:obs[(2026,town,party,ss,sr,cg,'requested',req,'')]+=1
        if (r.get('Status') or '').strip().upper() in ACCEPT and date(r.get('Rec Date','')):
            accepted_unique+=1
            obs[(2026,town,party,ss,sr,cg,'returned',date(r['Rec Date']),(r.get('Rec Type') or '').strip().upper())]+=1
    return [list(k)+[v] for k,v in sorted(obs.items())],dict(source_rows=source_rows,unique_voters=len(voters),skipped_rows=skipped,statuses=dict(statuses),accepted_unique=accepted_unique)
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--data',required=True);p.add_argument('--output',required=True);p.add_argument('--check-only',action='store_true');a=p.parse_args()
    existing=json.load(open(a.data));new,audit=build(a.source)
    old=[r for r in existing['observations'] if r[0]==2026]
    old_counts=collections.Counter({tuple(r[:-1]):r[-1] for r in old});new_counts=collections.Counter({tuple(r[:-1]):r[-1] for r in new})
    print('Existing audit:',existing['audit'].get('2026'))
    print('Rebuilt audit:',audit)
    print('Existing 2026 totals:',sum(r[-1] for r in old if r[6]=='requested'),sum(r[-1] for r in old if r[6]=='returned'))
    print('Rebuilt 2026 totals:',sum(r[-1] for r in new if r[6]=='requested'),sum(r[-1] for r in new if r[6]=='returned'))
    print('Differing aggregate keys:',sum(old_counts[k]!=new_counts[k] for k in old_counts.keys()|new_counts.keys()))
    if a.check_only:return
    if audit['unique_voters']<100000 or audit['accepted_unique']<100000:raise ValueError('Unexpectedly small source data; refusing publication')
    if old_counts!=new_counts:
        raise ValueError('Rebuild does not match existing reference data. No output published; investigate differences.')
    existing['observations']=[r for r in existing['observations'] if r[0]!=2026]+new
    existing['audit']['2026']=audit
    pathlib.Path(a.output).write_text(json.dumps(existing,separators=(',',':')),encoding='utf-8')
    print('Validated output:',a.output)
if __name__=='__main__':main()
