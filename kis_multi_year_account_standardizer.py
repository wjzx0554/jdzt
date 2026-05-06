# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, csv, json, os, re, shutil, tempfile, platform
from pathlib import Path
from collections import defaultdict

LEDGER_SUFFIXES={'.ais','.aiy','.axx','.mdb'}
DEFAULT_CONFIG={
 'account_table_candidates':['GLAcct','t_Account','Account','Acct'],
 'account_code_candidates':['FAcctID','FNumber','FCode','AcctCode','AccountCode','科目编码'],
 'account_name_candidates':['FName','FAcctName','FAccountName','FDetailName','科目名称'],
 'account_fullname_candidates':['FFullName','FullName','科目全名'],
 'parent_code_candidates':['FParentID','FParentCode','ParentCode','上级科目编码'],
 'known_reference_fields':{'GLBal':['FAcctID','FNumber','FCode'],'GLVch':['FAcctID','FNumber','FCode'],'GLVchEntry':['FAcctID','FNumber','FCode'],'GLObj':['FAcctID','FNumber','FCode'],'GLCash':['FAcctID','FNumber','FCode'],'GLMultiBal':['FAcctID','FNumber','FCode']},
 'auto_reference_field_names':['FAcctID','FNumber','FCode','AcctCode','AccountCode'],
 'voucher_reference_tables':['GLVch','GLVchEntry'],
 'balance_reference_tables':['GLBal','GLMultiBal'],
 'year_dedicated_child_suffix':'专用',
 'generated_code_width':2
}

def load_config(path=None):
    cfg=dict(DEFAULT_CONFIG)
    if path and Path(path).exists():
        with open(path,'r',encoding='utf-8-sig') as f: user=json.load(f)
        for k,v in user.items():
            if k=='known_reference_fields':
                x=dict(cfg[k]); x.update(v); cfg[k]=x
            else: cfg[k]=v
    return cfg

def year_of(p):
    m=re.search(r'(19|20)\d{2}',str(p)); return m.group(0) if m else ''

def ledgers(root):
    return sorted([p for p in Path(root).rglob('*') if p.is_file() and p.suffix.lower() in LEDGER_SUFFIXES])

def write_csv(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    keys=[]
    for r in rows:
        for k in r.keys():
            if k not in keys: keys.append(k)
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def read_csv(path):
    with open(path,'r',encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

class AccessDB:
    def __init__(self,cfg): self.cfg=cfg
    def connect(self,file_path):
        import pyodbc
        installed=list(pyodbc.drivers())
        cand=[]
        for d in ['Microsoft Access Driver (*.mdb, *.accdb)','Microsoft Access Driver (*.mdb)']+installed:
            dl=d.lower()
            if 'access' in dl and ('.mdb' in dl or '.accdb' in dl or 'microsoft access' in dl):
                if d not in cand: cand.append(d)
        if not cand:
            raise RuntimeError('当前 EXE/Python 位数下没有发现 Access ODBC 驱动。位数：%s；可见驱动：%s。老账套建议使用 x86 版本和 32 位 Access/Jet 驱动。'%(platform.architecture()[0],installed))
        paths=[file_path]; tmp=None
        if str(file_path).lower().endswith(('.ais','.aiy','.axx')):
            try:
                td=tempfile.mkdtemp(prefix='kis_ais_'); tmp=os.path.join(td,Path(file_path).name+'.mdb'); shutil.copy2(file_path,tmp); paths.append(tmp)
            except Exception: pass
        last=None
        for p in paths:
            for d in cand:
                try: return pyodbc.connect('DRIVER={%s};DBQ=%s;'%(d,p),autocommit=False)
                except Exception as e: last=e
        raise RuntimeError('无法连接账套：%s\nEXE/Python 位数：%s\n已发现 Access 驱动：%s\n最后错误：%s'%(file_path,platform.architecture()[0],cand,last))
    def tables(self,conn):
        return sorted(set([r.table_name for r in conn.cursor().tables(tableType='TABLE') if r.table_name and not str(r.table_name).startswith('MSys')]))
    def cols(self,conn,t): return [r.column_name for r in conn.cursor().columns(table=t)]
    def pick(self,cols,cands):
        m={c.lower():c for c in cols}
        for x in cands:
            if x.lower() in m: return m[x.lower()]
        return None
    def account_table(self,conn):
        tabs=self.tables(conn); low={t.lower():t for t in tabs}; table=None
        for c in self.cfg['account_table_candidates']:
            if c.lower() in low: table=low[c.lower()]; break
        if not table:
            for t in tabs:
                cs=self.cols(conn,t)
                if self.pick(cs,self.cfg['account_code_candidates']) and self.pick(cs,self.cfg['account_name_candidates']): table=t; break
        if not table: raise RuntimeError('无法识别科目表，请调整 config.template.json')
        cs=self.cols(conn,table)
        f={'code':self.pick(cs,self.cfg['account_code_candidates']),'name':self.pick(cs,self.cfg['account_name_candidates']),'fullname':self.pick(cs,self.cfg['account_fullname_candidates']),'parent':self.pick(cs,self.cfg['parent_code_candidates'])}
        if not f['code'] or not f['name']: raise RuntimeError('科目表无法识别编码/名称字段：%s %s'%(table,cs))
        return table,f
    def count_usage(self,conn,code):
        vc=bc=0; tabs=set(self.tables(conn))
        for group,typ in [('voucher_reference_tables','v'),('balance_reference_tables','b')]:
            for t in self.cfg.get(group,[]):
                if t not in tabs: continue
                cs=self.cols(conn,t)
                for c in self.cfg['auto_reference_field_names']:
                    if c in cs:
                        try:
                            cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(t,c),code); n=int(cur.fetchone()[0] or 0)
                            if typ=='v': vc+=n
                            else: bc+=n
                        except Exception: pass
        return vc,bc
    def read_accounts(self,file):
        conn=self.connect(str(file))
        try:
            table,f=self.account_table(conn); fields=[x for x in f.values() if x]
            cur=conn.cursor(); cur.execute('SELECT '+','.join('[%s]'%x for x in fields)+' FROM [%s]'%table)
            out=[]
            for row in cur.fetchall():
                d={fields[i]:'' if row[i] is None else str(row[i]).strip() for i in range(len(fields))}
                code=d.get(f['code'],''); name=d.get(f['name'],'')
                if not code: continue
                vc,bc=self.count_usage(conn,code)
                out.append({'ledger_file':str(file),'ledger_name':Path(file).stem,'year':year_of(file),'account_table':table,'old_code':code,'old_name':name,'old_full_name':d.get(f.get('fullname') or '', ''),'old_parent_code':d.get(f.get('parent') or '', ''),'used_in_voucher':vc,'used_in_balance':bc})
            schema=[{'账套文件':str(file),'表名':t,'字段':', '.join(self.cols(conn,t)),'是否科目表':'Y' if t==table else ''} for t in self.tables(conn)]
            return out,schema
        finally: conn.close()
    def refs(self,conn):
        out=[]; known=self.cfg.get('known_reference_fields',{})
        for t in self.tables(conn):
            cs=self.cols(conn,t)
            for c in known.get(t,[])+self.cfg['auto_reference_field_names']:
                if c in cs and (t,c) not in out: out.append((t,c))
        return out
    def apply(self,src,dst,maps,dry=True):
        target=src if dry else dst
        if not dry:
            Path(dst).parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
        conn=self.connect(target); audit=[]
        try:
            table,f=self.account_table(conn); cf,nf,pf=f['code'],f['name'],f.get('parent'); cur=conn.cursor(); refs=self.refs(conn)
            for m in maps:
                old=m['old_code'].strip(); new=m['new_code'].strip(); name=m['new_name'].strip(); parent=m.get('new_parent_code','').strip()
                if not old or not new: continue
                if old!=new:
                    for t,c in refs:
                        try:
                            cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(t,c),old); cnt=int(cur.fetchone()[0] or 0)
                            audit.append({'file':src,'table':t,'field':c,'action':'update_reference','old_code':old,'new_code':new,'affected':cnt})
                            if cnt and not dry: cur.execute('UPDATE [%s] SET [%s]=? WHERE [%s]=?'%(t,c,c),new,old)
                        except Exception as e: audit.append({'file':src,'table':t,'field':c,'action':'error','error':str(e)})
                try:
                    cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(table,cf),new); exists=int(cur.fetchone()[0] or 0)>0
                    if old==new:
                        audit.append({'file':src,'table':table,'action':'update_account_name','code':old,'new_name':name})
                        if not dry: cur.execute('UPDATE [%s] SET [%s]=? WHERE [%s]=?'%(table,nf,cf),name,old)
                    elif exists:
                        audit.append({'file':src,'table':table,'action':'mapped_to_existing_target','old_code':old,'new_code':new,'note':'引用已改到目标科目，旧科目不自动删除'})
                    else:
                        audit.append({'file':src,'table':table,'action':'rename_account','old_code':old,'new_code':new,'new_name':name})
                        if not dry:
                            sql='UPDATE [%s] SET [%s]=?, [%s]=?'%(table,cf,nf); vals=[new,name]
                            if pf and parent: sql+=', [%s]=?'%pf; vals.append(parent)
                            sql+=' WHERE [%s]=?'%cf; vals.append(old); cur.execute(sql,vals)
                except Exception as e: audit.append({'file':src,'table':table,'action':'account_error','old_code':old,'new_code':new,'error':str(e)})
            conn.rollback() if dry else conn.commit(); return audit
        except Exception:
            conn.rollback(); raise
        finally: conn.close()

def norm(s): return re.sub(r'\s+','',(s or '').strip())
def next_code(existing,base,parent='',width=2):
    p=parent or (base[:-2] if len(base)>4 else '')
    if p:
        nums=[int(c[len(p):]) for c in existing if c.startswith(p) and len(c)==len(p)+width and c[len(p):].isdigit()]
        n=max(nums or [0])+1
        while True:
            c=p+str(n).zfill(width)
            if c not in existing: existing.add(c); return c
            n+=1
    n=max([int(c) for c in existing if c.isdigit() and len(c)==len(base)] or [int(base) if base.isdigit() else 0])+1
    while True:
        c=str(n).zfill(len(base)) if base.isdigit() else base+'_'+str(n)
        if c not in existing: existing.add(c); return c
        n+=1

def build_plan(accounts,cfg):
    acc=sorted(accounts,key=lambda a:(a.get('year') or '9999',a['old_code'],a['old_name']))
    existing={a['old_code'] for a in acc if a['old_code']}; by_code=defaultdict(list); by_name=defaultdict(list)
    for a in acc: by_code[a['old_code']].append(a); by_name[norm(a['old_name'])].append(a)
    name_std={}
    for nm,rows in by_name.items():
        if nm:
            r=sorted(rows,key=lambda x:(x.get('year') or '9999',x['old_code']))[0]; name_std[nm]=(r['old_code'],r['old_name'])
    maps=[]; ex=[]
    for code,rows in by_code.items():
        names=list(dict.fromkeys([r['old_name'] for r in rows]))
        if len(names)>1: ex.append({'type':'同编码多名称','code':code,'names':' | '.join(names),'suggestion':'除第一个名称外，其余名称建议重新编码'})
    for a in acc:
        new_code=a['old_code']; new_name=a['old_name']; action='keep'; conflict=''; reason=''; parent=a.get('old_parent_code','')
        nm=norm(a['old_name'])
        if nm in name_std and a['old_code']!=name_std[nm][0]:
            new_code,new_name=name_std[nm]; action='map_to_existing'; conflict='同名称多编码'; reason='默认映射到最早出现编码，需人工确认'
        names=list(dict.fromkeys([r['old_name'] for r in by_code[a['old_code']]]))
        if len(names)>1 and a['old_name']!=names[0]:
            new_code=next_code(existing,a['old_code'],parent,int(cfg.get('generated_code_width',2))); new_name=a['old_name']; action='recode'; conflict='同编码多名称'; reason='同一编码对应不同科目名称，必须重新编码'
        maps.append({**a,'new_code':new_code,'new_name':new_name,'new_parent_code':parent,'action':action,'conflict_type':conflict,'reason':reason,'confirmed':'N' if conflict else 'Y'})
    final_codes={m['new_code'] for m in maps}
    for m in maps:
        if int(m.get('used_in_voucher') or 0)>0 and any(c!=m['new_code'] and c.startswith(m['new_code']) for c in final_codes):
            nc=next_code(existing,m['new_code']+'01',m['new_code'],int(cfg.get('generated_code_width',2)))
            m.update({'new_code':nc,'new_name':m['old_name']+'-'+(m.get('year') or '')+cfg.get('year_dedicated_child_suffix','专用'),'new_parent_code':m['old_code'],'action':'create_year_dedicated_child','conflict_type':'父级科目被凭证使用','reason':'该科目在其他年度存在下级，但本年度凭证直接使用父级','confirmed':'N'})
    std={m['new_code']:{'科目编码':m['new_code'],'科目名称':m['new_name'],'上级科目编码':m.get('new_parent_code','')} for m in maps}
    return maps,[std[k] for k in sorted(std)],ex

def cmd_make_config(a):
    with open(a.out,'w',encoding='utf-8') as f: json.dump(DEFAULT_CONFIG,f,ensure_ascii=False,indent=2)
    print('已生成配置：',a.out)
def cmd_inspect(a):
    cfg=load_config(a.config); db=AccessDB(cfg); accounts=[]; schemas=[]; errs=[]
    for f in ledgers(a.input):
        print('读取',f)
        try:
            x,s=db.read_accounts(f); accounts+=x; schemas+=s
        except Exception as e: errs.append({'账套文件':str(f),'错误':str(e)})
    out=Path(a.out); write_csv(out/'01_账套表结构清单.csv',schemas); write_csv(out/'02_多年科目汇总.csv',accounts)
    if errs: write_csv(out/'00_读取错误.csv',errs)
    maps,std,ex=build_plan(accounts,cfg); write_csv(out/'03_标准科目表_草稿.csv',std); write_csv(out/'04_科目旧新映射_草稿.csv',maps); write_csv(out/'05_冲突和需人工确认清单.csv',ex)
    print('完成，输出目录：',out)
def cmd_apply(a):
    cfg=load_config(a.config); db=AccessDB(cfg); rows=read_csv(a.mapping); by=defaultdict(list); skipped=[]
    for r in rows:
        if not a.allow_unconfirmed and str(r.get('confirmed','')).upper()!='Y': skipped.append(r); continue
        by[r['ledger_file']].append(r)
    audit=[]; out=Path(a.out)
    for src,maps in by.items():
        print(('试运行 ' if a.dry_run else '写入副本 ')+src)
        try: audit+=db.apply(src,str(out/Path(src).name),maps,a.dry_run)
        except Exception as e: audit.append({'file':src,'action':'apply_error','error':str(e)})
    write_csv(out/('apply_audit_dryrun.csv' if a.dry_run else 'apply_audit_commit.csv'),audit)
    if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
    print('完成。')
def main():
    p=argparse.ArgumentParser(description='金蝶 KIS 多年账套科目标准化工具')
    sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('make-config'); q.add_argument('--out',required=True); q.set_defaults(func=cmd_make_config)
    q=sub.add_parser('inspect'); q.add_argument('--input',required=True); q.add_argument('--out',required=True); q.add_argument('--config'); q.set_defaults(func=cmd_inspect)
    q=sub.add_parser('apply'); q.add_argument('--mapping',required=True); q.add_argument('--out',required=True); q.add_argument('--config'); q.add_argument('--dry-run',action='store_true',default=True); q.add_argument('--commit',dest='dry_run',action='store_false'); q.add_argument('--allow-unconfirmed',action='store_true'); q.set_defaults(func=cmd_apply)
    a=p.parse_args(); a.func(a)
if __name__=='__main__': main()
