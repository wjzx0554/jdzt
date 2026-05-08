# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, csv, json, os, re, shutil, tempfile, platform, sys, time, hashlib
from pathlib import Path
from collections import defaultdict

LEDGER_SUFFIXES={'.ais','.aiy','.axx','.mdb'}
DEFAULT_CONFIG={
 'account_table_candidates':['GLAcct','t_Account','Account','Acct'],
 'account_code_candidates':['FAcctID','FNumber','FCode','AcctCode','AccountCode','科目编码'],
 'account_name_candidates':['FName','FAcctName','FAccountName','FDetailName','科目名称'],
 'account_fullname_candidates':['FFullName','FullName','科目全名'],
 'parent_code_candidates':['FParentID','FParentCode','ParentCode','上级科目编码'],
 'known_reference_fields':{'GLAcct':['FAcctID'],'GLVch':['FAcctID'],'GLVchEntry':['FAcctID'],'GLBal':['FAcctID'],'GLBalHist':['FAcctID'],'GLInitBal':['FAcctID'],'GLInitData':['FAcctID'],'GLPref':['FCashAc','FBankAc','FExchAc','FEarnAc','FAssetAc','FDeprAc','FProfitAcctID','FLossAcctID','FYearProfitAcctID','FYearLossAcctID','FExchangeGainAcctID','FExchangeLossAcctID','FCashAcctID','FBankAcctID','FTaxAcctID','FDefaultAcctID','FPLAcctID','FDefAcctID']},
 'auto_reference_field_names':['FAcctID','FNumber','FCode','AcctCode','AccountCode'],
 'voucher_reference_tables':['GLVch','GLVchEntry'],
 'balance_reference_tables':['GLBal','GLBalHist','GLInitBal','GLMultiBal'],
 'year_dedicated_child_suffix':'专用',
 'generated_code_width':2,
 'access_systemdb':'',
 'access_systemdb_candidates':['System.mda','临时存储文件/System.mda'],
 'access_uid':'morningstar',
 'access_pwd':'ypbwkfyjhyhgzj',
 'access_text_encoding':'gb18030',
 'auto_expand_account_levels':True,
 'max_account_levels':6
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

def load_config_for_args(a):
    cfg=load_config(getattr(a,'config',None))
    systemdb=getattr(a,'systemdb',None)
    if systemdb: cfg['access_systemdb']=systemdb
    return cfg

def year_of(p):
    path=Path(p)
    parts=[path.stem]+[x.name for x in path.parents if x.name]
    for part in parts:
        years=re.findall(r'(?:19|20)\d{2}',str(part))
        if len(years)==1: return years[0]
    m=re.search(r'(?:19|20)\d{2}',str(p))
    return m.group(0) if m else ''

def ledgers(root):
    return sorted([p for p in Path(root).rglob('*') if p.is_file() and p.suffix.lower() in LEDGER_SUFFIXES])

def short_path_id(path):
    return hashlib.md5(str(path).encode('utf-8','ignore')).hexdigest()[:8]

def source_file_id(path):
    try: raw=str(Path(path).resolve()).lower()
    except Exception: raw=str(path).lower()
    return short_path_id(raw)

def clean_group_text(text):
    s=sql_value(text)
    s=re.sub(r'(?:19|20)\d{2}', '', s)
    s=re.sub(r'[_\-—\s]+', '', s)
    return s

def ledger_group_name(file_path,company_name=''):
    company=sql_value(company_name)
    if company: return company
    p=Path(file_path)
    for part in [p.stem,p.parent.name,p.parent.parent.name if p.parent else '']:
        cleaned=clean_group_text(part)
        if cleaned: return cleaned
    return p.stem or '未命名账套组'

def ledger_output_paths(out,sources,source_years=None):
    out=Path(out); result={}; used=set(); source_years=source_years or {}
    for src in sorted([s for s in sources if s]):
        p=Path(src); yr=sql_value(source_years.get(src,'')) or year_of(src)
        if yr and yr not in p.stem:
            candidate=out/(p.stem+'_'+yr+p.suffix)
        else:
            candidate=out/p.name
        key=str(candidate).lower()
        if key in used:
            candidate=candidate.with_name(candidate.stem+'_'+short_path_id(src)+candidate.suffix)
            key=str(candidate).lower()
        used.add(key); result[src]=str(candidate)
    return result

def write_csv(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    keys=[]
    for r in rows:
        for k in r.keys():
            if k not in keys: keys.append(k)
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def write_csv_columns(path,rows,columns):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    keys=list(columns)
    for r in rows:
        for k in r.keys():
            if k not in keys: keys.append(k)
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def read_csv(path):
    with open(path,'r',encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def app_dir():
    if getattr(sys,'frozen',False): return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def is_access_odbc_driver(name):
    dl=str(name).lower()
    if any(x in dl for x in ('text','txt','csv','excel','xls','dbase','dbf','paradox')):
        return False
    return 'access' in dl and '.mdb' in dl

def odbc_value(value):
    s='' if value is None else str(value)
    if any(ch in s for ch in ';{}') or s!=s.strip():
        return '{%s}'%s.replace('}','}}')
    return s

def access_conn_str(driver,dbq,opts):
    parts=['DRIVER={%s}'%driver,'DBQ=%s'%odbc_value(dbq)]
    for k,v in opts: parts.append('%s=%s'%(k,odbc_value(v)))
    return ';'.join(parts)+';'

def find_systemdb(cfg,file_path):
    raw=[]
    if os.environ.get('KIS_SYSTEMDB'): raw.append(os.environ['KIS_SYSTEMDB'])
    if cfg.get('access_systemdb'): raw.append(cfg.get('access_systemdb'))
    for item in raw:
        if item and Path(item).exists() and Path(item).is_file(): return str(Path(item))
    roots=[app_dir(),Path.cwd(),Path(file_path).resolve().parent]
    raw=list(cfg.get('access_systemdb_candidates') or [])
    seen=set()
    for item in raw:
        if not item: continue
        p=Path(item)
        checks=[p] if p.is_absolute() else [r/p for r in roots]
        for c in checks:
            try: key=str(c.resolve()).lower()
            except Exception: key=str(c).lower()
            if key in seen: continue
            seen.add(key)
            if c.exists() and c.is_file(): return str(c)
    return ''

def configure_access_decoding(conn,cfg):
    encoding=str(cfg.get('access_text_encoding') or '').strip()
    if not encoding: return
    try:
        import pyodbc
        for sql_type in [getattr(pyodbc,'SQL_CHAR',None),getattr(pyodbc,'SQL_WCHAR',None)]:
            if sql_type is not None:
                try: conn.setdecoding(sql_type,encoding=encoding)
                except Exception: pass
        try: conn.setencoding(encoding=encoding)
        except Exception: pass
    except Exception: pass

def access_login_options(cfg,systemdb):
    out=[]; seen=set()
    def add(opts,label):
        key=tuple(opts)
        if key not in seen:
            seen.add(key); out.append((opts,label))
    if systemdb:
        uid=str(cfg.get('access_uid') or 'morningstar'); pwd=str(cfg.get('access_pwd') or 'ypbwkfyjhyhgzj')
        add([('SystemDB',systemdb),('UID',uid),('PWD',pwd)],'System.mda + %s'%uid)
        add([('SystemDB',systemdb),('UID','morningstar'),('PWD','ypbwkfyjhyhgzj')],'System.mda + morningstar')
        add([('SystemDB',systemdb),('UID','Admin'),('PWD','')],'System.mda + Admin')
    add([],'无 System.mda')
    return out

def compact_error(e):
    return re.sub(r'\s+',' ',str(e)).strip()

def format_attempts(attempts,limit=10):
    if not attempts: return ''
    rows=[]
    for a in attempts[-limit:]:
        rows.append(' - %s | %s | %s -> %s'%(Path(a['path']).name,a['driver'],a['login'],a['error'][:360]))
    more=len(attempts)-limit
    if more>0: rows.insert(0,' - 前面还有 %s 次尝试，已省略。'%more)
    return '\n'.join(rows)

def ledger_header_hint(file_path,systemdb):
    try: head=Path(file_path).read_bytes()[:128]
    except Exception: return ''
    if b'Standard Jet DB' in head: return ''
    if str(file_path).lower().endswith(('.ais','.aiy','.axx')):
        if systemdb:
            return '检测到账套不是普通 MDB 文件头；这类金蝶老 AIS 通常必须使用 x86 工具 + 32 位 Jet 驱动 + System.mda。'
        return '检测到账套不是普通 MDB 文件头；这类金蝶老 AIS 通常必须提供金蝶 System.mda 工作组文件。'
    return ''

def sql_value(v):
    if v is None: return ''
    return str(v).strip()

def year_from_value(v):
    m=re.search(r'(?:19|20)\d{2}',sql_value(v))
    return m.group(0) if m else ''

def parent_from_code(code,lengths):
    code=sql_value(code)
    if not code: return ''
    smaller=[x for x in lengths if x<len(code)]
    return code[:max(smaller)] if smaller else ''

def level_from_code(code,lengths):
    code=sql_value(code)
    if not code: return ''
    try: return str(sorted(lengths).index(len(code))+1)
    except Exception: return ''

def code_lengths(codes):
    return sorted(set([len(sql_value(c)) for c in codes if sql_value(c)]))

def mapping_code_lengths(rows):
    codes=[]
    for r in rows:
        for k in ['new_code','new_parent_code','old_code','old_parent_code']:
            v=sql_value(r.get(k,''))
            if v: codes.append(v)
    return code_lengths(codes)

def required_parent_codes(code,lengths):
    code=sql_value(code)
    out=[]
    for l in sorted(set(int(x) for x in lengths if str(x).isdigit() or isinstance(x,int))):
        if l>=len(code): break
        parent=code[:l]
        if parent: out.append(parent)
    return out

def missing_parent_codes(codes,lengths):
    code_set=set(sql_value(c) for c in codes if sql_value(c))
    missing=set()
    for code in code_set:
        for parent in required_parent_codes(code,lengths):
            if parent not in code_set: missing.add(parent)
    return sorted(missing)

def row_value(row,*keys):
    for k in keys:
        v=sql_value(row.get(k,''))
        if v: return v
    return ''

def normalize_mapping_row(r):
    out=dict(r)
    aliases={
        'source_file':['source_file','ledger_file','账套文件'],
        'source_file_id':['source_file_id','账套文件ID'],
        'company':['company','company_name','公司名称'],
        'year':['year','年度'],
        'old_code':['old_code','旧科目编码','原始科目编码'],
        'old_name':['old_name','旧科目名称','原始科目名称'],
        'new_code':['new_code','新科目编码'],
        'new_name':['new_name','新科目名称'],
        'new_parent_code':['new_parent_code','新父级编码','父级编码'],
        'action':['action','处理动作'],
        'confirmed':['confirmed','是否确认'],
        'risk_level':['risk_level','风险级别'],
        'source_db_table':['source_db_table','来源表'],
        'source_code_field':['source_code_field','编码字段'],
        'source_name_field':['source_name_field','名称字段'],
    }
    for key,ks in aliases.items(): out[key]=row_value(r,*ks)
    if out.get('source_file') and not out.get('source_file_id'): out['source_file_id']=source_file_id(out['source_file'])
    if out.get('new_code') and not out.get('new_parent_code'): out['new_parent_code']=row_value(r,'old_parent_code','父级编码')
    return out

def pref_lengths_from_row(row):
    out=[]
    for k in ['FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6']:
        v=sql_value(row.get(k,''))
        if v.isdigit() and int(v)>0: out.append(int(v))
    return sorted(set(out))

def pref_values_from_row(row,prefix=''):
    return {prefix+k:sql_value(row.get(k,'')) for k in ['FAcLevels','FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6']}

def yn(v): return 'Y' if v else 'N'

def audit_row(source_file='',table='',field='',old_code='',old_name='',new_code='',new_name='',action='',risk_level='info',reason='',planned_sql_type='',affected_rows='',dry_run='',blocked_commit='',**extra):
    row={'source_file':source_file,'table':table,'field':field,'old_code':old_code,'old_name':old_name,'new_code':new_code,'new_name':new_name,'action':action,'risk_level':risk_level,'reason':reason,'planned_sql_type':planned_sql_type,'affected_rows':affected_rows,'dry_run':dry_run,'blocked_commit':blocked_commit}
    row.update(extra); return row

def is_blocking(row):
    return str(row.get('blocked_commit','')).upper()=='Y' or str(row.get('risk_level','')).lower() in ('error','high')

MERGE_ACTIONS={'map_to_existing','mapped_to_existing_target','merge_to_existing'}

class ApplyError(RuntimeError):
    def __init__(self,message,audit=None):
        super().__init__(message); self.audit=audit or []

class AccessDB:
    def __init__(self,cfg): self.cfg=cfg
    def connect(self,file_path):
        import pyodbc
        installed=list(pyodbc.drivers())
        cand=[]; installed_lower={str(d).lower():d for d in installed}
        preferred=[installed_lower[p.lower()] for p in ['Microsoft Access Driver (*.mdb, *.accdb)','Microsoft Access Driver (*.mdb)'] if p.lower() in installed_lower]
        for d in preferred+installed:
            if is_access_odbc_driver(d):
                if d not in cand: cand.append(d)
        if not cand:
            raise RuntimeError('当前 EXE/Python 位数下没有发现 Access ODBC 驱动。位数：%s；可见驱动：%s。老账套建议使用 x86 版本和 32 位 Access/Jet 驱动。'%(platform.architecture()[0],installed))
        paths=[file_path]; tmp=None
        if str(file_path).lower().endswith(('.ais','.aiy','.axx')):
            try:
                td=tempfile.mkdtemp(prefix='kis_ais_'); tmp=os.path.join(td,Path(file_path).name+'.mdb'); shutil.copy2(file_path,tmp); paths.append(tmp)
            except Exception: pass
        systemdb=find_systemdb(self.cfg,file_path)
        logins=access_login_options(self.cfg,systemdb)
        last=None; attempts=[]
        for p in paths:
            for d in cand:
                for opts,label in logins:
                    try:
                        conn=pyodbc.connect(access_conn_str(d,p,opts),autocommit=False)
                        configure_access_decoding(conn,self.cfg)
                        return conn
                    except Exception as e:
                        last=e; attempts.append({'path':p,'driver':d,'login':label,'error':compact_error(e)})
        hints=[]
        if platform.architecture()[0]=='64bit':
            hints.append('当前是 64bit。2001—2007 年金蝶老 AIS 优先运行 KIS_MultiYear_Standardizer_GUI_x86.exe，并安装/使用 32 位 Microsoft Access Driver (*.mdb)。')
        if not systemdb:
            hints.append('未找到 System.mda。请把金蝶安装目录或“金蝶引出处理工具”的“临时存储文件/System.mda”放到 exe 同目录、exe 同目录的“临时存储文件”下，或在 config.json 设置 access_systemdb。')
        hh=ledger_header_hint(file_path,systemdb)
        if hh: hints.append(hh)
        raise RuntimeError('无法连接账套：%s\nEXE/Python 位数：%s\n已发现 Access 驱动：%s\nSystem.mda：%s\n提示：%s\n连接尝试：\n%s\n最后错误：%s'%(file_path,platform.architecture()[0],cand,systemdb or '未找到','；'.join(hints) or '无',format_attempts(attempts),last))
    def tables(self,conn):
        try:
            return sorted(set([r.table_name for r in conn.cursor().tables(tableType='TABLE') if r.table_name and not str(r.table_name).startswith('MSys')]))
        except Exception:
            out=[]
            for t in ['GLPref','GLCompany','GLAcct','GLBal','GLBalHist','GLInitBal','GLVch','GLObj','GLCash','GLMultiBal']:
                if self.table_exists(conn,t): out.append(t)
            return out
    def table_exists(self,conn,t):
        try:
            conn.cursor().execute('SELECT * FROM [%s] WHERE 1=0'%t)
            return True
        except Exception: return False
    def cols(self,conn,t):
        cols=[]
        try:
            cols=[r.column_name for r in conn.cursor().columns(table=t) if r.column_name]
        except Exception: pass
        if cols: return cols
        cur=conn.cursor(); cur.execute('SELECT * FROM [%s] WHERE 1=0'%t)
        return [d[0] for d in cur.description]
    def fast_table_exists(self,conn,t):
        try:
            conn.cursor().execute('SELECT COUNT(*) AS N FROM [%s] WHERE 1=0'%t)
            return True
        except Exception: return False
    def fast_field_exists(self,conn,t,c):
        try:
            conn.cursor().execute('SELECT [%s] FROM [%s] WHERE 1=0'%(c,t))
            return True
        except Exception: return False
    def fast_existing_fields(self,conn,t,candidates):
        out=[]; seen=set()
        for c in candidates:
            key=str(c).lower()
            if key in seen: continue
            seen.add(key)
            if self.fast_field_exists(conn,t,c): out.append(c)
        return out
    def fast_pick_field(self,conn,t,candidates):
        fs=self.fast_existing_fields(conn,t,candidates)
        return fs[0] if fs else ''
    def fast_one_known(self,conn,t,candidates):
        fields=self.fast_existing_fields(conn,t,candidates)
        if not fields: return {},fields
        return self.one(conn,'SELECT '+','.join('[%s]'%x for x in fields)+' FROM [%s]'%t),fields
    def fast_rows_known(self,conn,t,candidates):
        fields=self.fast_existing_fields(conn,t,candidates)
        if not fields: return [],fields
        cur=conn.cursor(); cur.execute('SELECT '+','.join('[%s]'%x for x in fields)+' FROM [%s]'%t)
        return [{fields[i]:sql_value(row[i]) for i in range(len(fields))} for row in cur.fetchall()],fields
    def first_of(self,row,candidates):
        for c in candidates:
            v=sql_value(row.get(c,''))
            if v: return v
        return ''
    def pick(self,cols,cands):
        m={c.lower():c for c in cols}
        for x in cands:
            if x.lower() in m: return m[x.lower()]
        return None
    def account_table(self,conn):
        table=None
        for c in self.cfg['account_table_candidates']:
            try:
                cs=self.cols(conn,c)
                if self.pick(cs,self.cfg['account_code_candidates']) and self.pick(cs,self.cfg['account_name_candidates']):
                    table=c; break
            except Exception: pass
        if not table:
            tabs=self.tables(conn); low={t.lower():t for t in tabs}
            for c in self.cfg['account_table_candidates']:
                if c.lower() in low: table=low[c.lower()]; break
        if not table:
            tabs=self.tables(conn)
            for t in tabs:
                cs=self.cols(conn,t)
                if self.pick(cs,self.cfg['account_code_candidates']) and self.pick(cs,self.cfg['account_name_candidates']): table=t; break
        if not table: raise RuntimeError('无法识别科目表，请调整 config.template.json')
        cs=self.cols(conn,table)
        f={'code':self.pick(cs,self.cfg['account_code_candidates']),'name':self.pick(cs,self.cfg['account_name_candidates']),'fullname':self.pick(cs,self.cfg['account_fullname_candidates']),'parent':self.pick(cs,self.cfg['parent_code_candidates'])}
        if not f['code'] or not f['name']: raise RuntimeError('科目表无法识别编码/名称字段：%s %s'%(table,cs))
        return table,f
    def count_usage(self,conn,code):
        vc=bc=0
        for group,typ in [('voucher_reference_tables','v'),('balance_reference_tables','b')]:
            for t in self.cfg.get(group,[]):
                if not self.table_exists(conn,t): continue
                cs=self.cols(conn,t)
                for c in self.cfg['auto_reference_field_names']:
                    if c in cs:
                        try:
                            cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(t,c),code); n=int(cur.fetchone()[0] or 0)
                            if typ=='v': vc+=n
                            else: bc+=n
                        except Exception: pass
        return vc,bc
    def one(self,conn,sql):
        cur=conn.cursor(); cur.execute(sql); row=cur.fetchone()
        if not row: return {}
        return {cur.description[i][0]:row[i] for i in range(len(cur.description))}
    def ledger_info(self,conn,file):
        info={'ledger_file':str(file),'ledger_name':Path(file).stem,'year':year_of(file),'company_name':'','start_year':'','current_year':'','start_period':'','current_period':'','voucher_start_date':'','voucher_end_date':''}
        try:
            r=self.one(conn,'SELECT FCompany,FStartYear,FStartPeriod,FCurrYear,FCurrPeriod,FNaturalStartYear FROM [GLPref]')
            info.update({'company_name':sql_value(r.get('FCompany')),'start_year':year_from_value(r.get('FStartYear')),'current_year':year_from_value(r.get('FCurrYear')),'start_period':sql_value(r.get('FStartPeriod')),'current_period':sql_value(r.get('FCurrPeriod'))})
        except Exception: pass
        try:
            r=self.one(conn,'SELECT MIN(FDate) AS MinDate, MAX(FDate) AS MaxDate FROM [GLVch]')
            info.update({'voucher_start_date':sql_value(r.get('MinDate')),'voucher_end_date':sql_value(r.get('MaxDate'))})
        except Exception: pass
        for y in [info.get('current_year'),info.get('start_year'),year_from_value(info.get('voucher_end_date')),year_from_value(info.get('voucher_start_date')),year_of(file)]:
            if y:
                info['year']=y; break
        return info
    def fast_voucher_bounds(self,conn):
        if not self.fast_table_exists(conn,'GLVch'): return {}
        selects=[]
        if self.fast_field_exists(conn,'GLVch','FDate'):
            selects+=['MIN([FDate]) AS voucher_start_date','MAX([FDate]) AS voucher_end_date']
        if self.fast_field_exists(conn,'GLVch','FYear'):
            selects+=['MIN([FYear]) AS voucher_min_year','MAX([FYear]) AS voucher_max_year']
        if self.fast_field_exists(conn,'GLVch','FPeriod'):
            selects+=['MIN([FPeriod]) AS voucher_min_period','MAX([FPeriod]) AS voucher_max_period']
        if not selects: return {}
        return self.one(conn,'SELECT '+','.join(selects)+' FROM [GLVch]')
    def scan_kis_file(self,file):
        started=time.perf_counter(); conn=None; accounts=[]; aux=[]; errors=[]; touched=[]
        fid=source_file_id(file)
        perf={'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'ledger_name':Path(file).stem,'scan_mode':'scan-kis','connected':'N','full_table_scan':'N','full_field_scan':'N','refs_called':'N','write_sql':'N','status':'error'}
        try:
            conn=self.connect(str(file)); perf['connected']='Y'
            pref={}; pref_fields=[]
            pref_candidates=['FCompany','FStartYear','FStartPeriod','FCurrYear','FCurrPeriod','FNaturalStartYear','FAcLevels','FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6']
            if self.fast_table_exists(conn,'GLPref'):
                pref,pref_fields=self.fast_one_known(conn,'GLPref',pref_candidates); touched.append('GLPref')
            else: errors.append({'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'stage':'GLPref','error':'GLPref 不存在'})
            vch={}
            if self.fast_table_exists(conn,'GLVch'):
                try: vch=self.fast_voucher_bounds(conn); touched.append('GLVch(MIN/MAX)')
                except Exception as e: errors.append({'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'stage':'GLVch','error':str(e)})
            info={'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'账套文件':str(file),'账套文件ID':fid,'ledger_name':Path(file).stem,'company':sql_value(pref.get('FCompany')),'company_name':sql_value(pref.get('FCompany')),'公司名称':sql_value(pref.get('FCompany')),'start_year':year_from_value(pref.get('FStartYear')),'current_year':year_from_value(pref.get('FCurrYear')),'start_period':sql_value(pref.get('FStartPeriod')),'current_period':sql_value(pref.get('FCurrPeriod')),'natural_start_year':year_from_value(pref.get('FNaturalStartYear')),'voucher_start_date':sql_value(vch.get('voucher_start_date')),'voucher_end_date':sql_value(vch.get('voucher_end_date')),'voucher_min_year':sql_value(vch.get('voucher_min_year')),'voucher_max_year':sql_value(vch.get('voucher_max_year')),'voucher_min_period':sql_value(vch.get('voucher_min_period')),'voucher_max_period':sql_value(vch.get('voucher_max_period')),'FAcLevels':sql_value(pref.get('FAcLevels')),'FAcLen1':sql_value(pref.get('FAcLen1')),'FAcLen2':sql_value(pref.get('FAcLen2')),'FAcLen3':sql_value(pref.get('FAcLen3')),'FAcLen4':sql_value(pref.get('FAcLen4')),'FAcLen5':sql_value(pref.get('FAcLen5')),'FAcLen6':sql_value(pref.get('FAcLen6'))}
            info['ledger_group']=ledger_group_name(file,info.get('company_name',''))
            for y in [info.get('current_year'),info.get('start_year'),year_from_value(info.get('voucher_max_year')),year_from_value(info.get('voucher_end_date')),year_of(file)]:
                if y: info['year']=y; break
            if 'year' not in info: info['year']=''
            info['年度']=info.get('year','')
            level_lengths=sorted(set([int(info[k]) for k in ['FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6'] if sql_value(info.get(k)).isdigit() and int(info[k])>0]))
            if self.fast_table_exists(conn,'GLAcct'):
                touched.append('GLAcct')
                cf=self.fast_pick_field(conn,'GLAcct',['FAcctID','FNumber','FCode','AcctCode','AccountCode'])
                nf=self.fast_pick_field(conn,'GLAcct',['FName','FAcctName','FAccountName','FDetailName'])
                ff=self.fast_pick_field(conn,'GLAcct',['FFullName','FullName'])
                pf=self.fast_pick_field(conn,'GLAcct',['FParentID','FParentCode','ParentCode'])
                if not cf or not nf:
                    errors.append({'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'stage':'GLAcct','error':'GLAcct 无法识别科目编码/名称字段'})
                else:
                    fields=[x for x in [cf,nf,ff,pf] if x]; rows,fields=self.fast_rows_known(conn,'GLAcct',fields); codes=[self.first_of(r,[cf]) for r in rows if self.first_of(r,[cf])]; lengths=level_lengths or code_lengths(codes)
                    for r in rows:
                        code=self.first_of(r,[cf]); name=self.first_of(r,[nf])
                        if not code: continue
                        parent=self.first_of(r,[pf]) if pf else parent_from_code(code,lengths); level=level_from_code(code,lengths)
                        accounts.append({**info,'原始科目编码':code,'原始科目名称':name,'父级编码':parent,'级次':level,'来源表':'GLAcct','编码字段':cf,'名称字段':nf,'source_db_table':'GLAcct','source_code_field':cf,'source_name_field':nf,'account_table':'GLAcct','account_code_field':cf,'account_name_field':nf,'old_code':code,'old_name':name,'old_full_name':self.first_of(r,[ff]) if ff else '','old_parent_code':parent,'level':level,'used_in_voucher':'','used_in_balance':'','voucher_usage_checked':'N','balance_usage_checked':'N'})
            else: errors.append({'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'stage':'GLAcct','error':'GLAcct 不存在'})
            aux_specs={
                'GLObj':['FObjID','FObjectID','FNumber','FCode','FAcctID','FName','FObjName','FItemName','FClassID','FType','FGroupID'],
                'GLCls':['FClsID','FClassID','FNumber','FCode','FName','FClsName','FClassName','FType'],
                'GLEmp':['FEmpID','FEmpCode','FNumber','FCode','FName','FEmpName','FDeptID','FGroupID'],
                'PAData':['FDataID','FItemID','FNumber','FCode','FAcctID','FName','FDataName','FItemName','FClassID','FType'],
                'PAItem':['FItemID','FNumber','FCode','FAcctID','FName','FItemName','FClassID','FType']
            }
            code_cands=['FObjID','FObjectID','FClsID','FClassID','FEmpID','FEmpCode','FDataID','FItemID','FNumber','FCode']
            name_cands=['FName','FObjName','FClsName','FClassName','FEmpName','FDataName','FItemName']
            type_cands=['FClassID','FType','FGroupID']
            for t,cands in aux_specs.items():
                if not self.fast_table_exists(conn,t): continue
                touched.append(t)
                try:
                    rows,fields=self.fast_rows_known(conn,t,cands)
                    for r in rows:
                        aux.append({**info,'source_table':t,'核算项目来源表':t,'item_code':self.first_of(r,code_cands),'item_name':self.first_of(r,name_cands),'item_type':self.first_of(r,type_cands),'account_code':self.first_of(r,['FAcctID'])})
                except Exception as e: errors.append({'source_file':str(file),'source_file_id':fid,'ledger_file':str(file),'stage':t,'error':str(e)})
            perf.update({'status':'ok','account_rows':len(accounts),'auxiliary_rows':len(aux),'touched_tables':';'.join(touched),'elapsed_ms':int((time.perf_counter()-started)*1000)})
            return accounts,aux,errors,perf
        except Exception as e:
            perf.update({'status':'error','error':str(e),'elapsed_ms':int((time.perf_counter()-started)*1000)})
            raise ApplyError(str(e),[perf]) from e
        finally:
            if conn: conn.close()
    def account_code_lengths(self,conn):
        try:
            r=self.account_level_pref(conn)
            return sorted(set([int(r[k]) for k in r if sql_value(r[k]).isdigit() and int(r[k])>0]))
        except Exception: return []
    def account_level_pref(self,conn):
        return self.one(conn,'SELECT FAcLevels,FAcLen1,FAcLen2,FAcLen3,FAcLen4,FAcLen5,FAcLen6 FROM [GLPref]')
    def desired_account_code_lengths(self,conn,maps,table='',code_field=''):
        codes=[]
        for n in self.cfg.get('_target_account_lengths') or []:
            try: codes.append('X'*int(n))
            except Exception: pass
        if table and code_field:
            try:
                cur=conn.cursor(); cur.execute('SELECT [%s] FROM [%s]'%(code_field,table))
                for row in cur.fetchall():
                    if row and row[0] is not None: codes.append(row[0])
            except Exception: pass
        for m in maps:
            for k in ['new_code','new_parent_code','old_code','old_parent_code']:
                v=sql_value(m.get(k,''))
                if v: codes.append(v)
        return code_lengths(codes)
    def ensure_account_levels(self,conn,cur,maps,table='',code_field='',dry=True):
        audit=[]
        if not self.cfg.get('auto_expand_account_levels',True): return audit
        if not self.table_exists(conn,'GLPref'): return audit
        desired=self.desired_account_code_lengths(conn,maps,table,code_field)
        if not desired: return audit
        current=self.account_level_lengths(conn)
        target=sorted(set((current or [])+desired))
        max_levels=int(self.cfg.get('max_account_levels',6) or 6)
        if len(target)>max_levels:
            msg='目标科目编码级次需要 %s 个不同长度：%s；KIS 迷你版通常最多支持 %s 级，请先调整映射编码。'%(len(target),target,max_levels)
            audit.append(audit_row(table='GLPref',action='account_level_error',risk_level='error',reason=msg,planned_sql_type='UPDATE_GLPREF',dry_run=yn(dry),blocked_commit='Y'))
            if not dry: raise RuntimeError(msg)
            return audit
        old_pref=self.account_level_pref(conn); old=current; padded=target+[0]*(6-len(target))
        audit.append(audit_row(table='GLPref',action='account_level_plan',risk_level='info',reason='更新 GLPref 科目级次，确保支持后续目标编码',planned_sql_type='UPDATE_GLPREF',affected_rows=1 if old!=target else 0,dry_run=yn(dry),blocked_commit='N',old_FAcLevels=sql_value(old_pref.get('FAcLevels')),old_FAcLen1=sql_value(old_pref.get('FAcLen1')),old_FAcLen2=sql_value(old_pref.get('FAcLen2')),old_FAcLen3=sql_value(old_pref.get('FAcLen3')),old_FAcLen4=sql_value(old_pref.get('FAcLen4')),old_FAcLen5=sql_value(old_pref.get('FAcLen5')),old_FAcLen6=sql_value(old_pref.get('FAcLen6')),new_FAcLevels=len(target),new_FAcLen1=padded[0],new_FAcLen2=padded[1],new_FAcLen3=padded[2],new_FAcLen4=padded[3],new_FAcLen5=padded[4],new_FAcLen6=padded[5],old_lengths=','.join(map(str,old)),new_lengths=','.join(map(str,target))))
        if old==target: return audit
        if not dry:
            cur.execute('UPDATE [GLPref] SET [FAcLevels]=?, [FAcLen1]=?, [FAcLen2]=?, [FAcLen3]=?, [FAcLen4]=?, [FAcLen5]=?, [FAcLen6]=?',len(target),padded[0],padded[1],padded[2],padded[3],padded[4],padded[5])
        return audit
    def schema_rows(self,conn,file,account_table):
        out=[]
        for t in self.tables(conn):
            try: cols=', '.join(self.cols(conn,t))
            except Exception as e: cols='读取字段失败：%s'%e
            out.append({'账套文件':str(file),'表名':t,'字段':cols,'是否科目表':'Y' if t==account_table else ''})
        return out
    def read_accounts(self,file):
        conn=self.connect(str(file))
        try:
            info=self.ledger_info(conn,file)
            code_lengths=self.account_code_lengths(conn)
            table,f=self.account_table(conn); fields=[x for x in f.values() if x]
            cur=conn.cursor(); cur.execute('SELECT '+','.join('[%s]'%x for x in fields)+' FROM [%s]'%table)
            out=[]
            for row in cur.fetchall():
                d={fields[i]:'' if row[i] is None else str(row[i]).strip() for i in range(len(fields))}
                code=d.get(f['code'],''); name=d.get(f['name'],'')
                if not code: continue
                vc,bc=self.count_usage(conn,code)
                parent=d.get(f.get('parent') or '', '') or parent_from_code(code,code_lengths)
                out.append({**info,'account_table':table,'old_code':code,'old_name':name,'old_full_name':d.get(f.get('fullname') or '', ''),'old_parent_code':parent,'used_in_voucher':vc,'used_in_balance':bc})
            schema=self.schema_rows(conn,file,table)
            return out,schema
        finally: conn.close()
    def reference_fields(self,conn,account_table='',account_code_field='',source_file='',dry=True):
        refs=[]; report=[]; known=self.cfg.get('known_reference_fields',{})
        for t,fields in known.items():
            if not fields: continue
            if not self.fast_table_exists(conn,t):
                continue
            for c in fields:
                if not self.fast_field_exists(conn,t,c):
                    continue
                if t.lower()==str(account_table).lower():
                    report.append(audit_row(source_file=source_file,table=t,field=c,action='reference_field_excluded',risk_level='info',reason='科目表字段由独立科目更新逻辑处理，不作为普通引用字段更新',planned_sql_type='NONE',dry_run=yn(dry),blocked_commit='N',safe='N'))
                else:
                    refs.append((t,c))
                    report.append(audit_row(source_file=source_file,table=t,field=c,action='reference_field_safe',risk_level='info',reason='字段在 known_reference_fields 白名单中，允许作为引用字段更新',planned_sql_type='UPDATE_REFERENCE',dry_run=yn(dry),blocked_commit='N',safe='Y'))
        if not report:
            report.append(audit_row(source_file=source_file,action='reference_fields_none',risk_level='info',reason='未识别到会更新的引用表字段',planned_sql_type='NONE',dry_run=yn(dry),blocked_commit='N',safe='Y'))
        return refs,report
    def refs(self,conn,account_table='',account_code_field='',source_file='',dry=True):
        return self.reference_fields(conn,account_table,account_code_field,source_file,dry)[0]
    def account_map(self,conn,table,code_field,name_field):
        out={}; cur=conn.cursor(); cur.execute('SELECT [%s], [%s] FROM [%s]'%(code_field,name_field,table))
        for row in cur.fetchall():
            code=sql_value(row[0])
            if code: out[code]=sql_value(row[1] if len(row)>1 else '')
        return out
    def final_code_set(self,accounts,rows):
        final=set(accounts.keys())
        for r in rows:
            old=sql_value(r.get('old_code','')); new=sql_value(r.get('new_code','')); action=sql_value(r.get('action','')).lower()
            if not old or not new: continue
            final.add(new)
            if old!=new and action not in MERGE_ACTIONS and old in final: final.remove(old)
        return final
    def add_preflight(self,out,row,check,risk,reason,dry,blocked=None,**extra):
        if blocked is None: blocked=risk in ('error','high')
        out.append(audit_row(source_file=row_value(row,'source_file','ledger_file'),source_file_id=row_value(row,'source_file_id'),year=row_value(row,'year'),old_code=sql_value(row.get('old_code','')),old_name=sql_value(row.get('old_name','')),new_code=sql_value(row.get('new_code','')),new_name=sql_value(row.get('new_name','')),action=sql_value(row.get('action','')),risk_level=risk,reason=reason,planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit=yn(blocked),check=check,confirmed=sql_value(row.get('confirmed','')),new_parent_code=sql_value(row.get('new_parent_code','')),**extra))
    def preflight_apply(self,src,dst,active_rows,all_rows,dry=True):
        pre=[]; ref_report=[]
        src_path=Path(src) if src else Path('')
        if not src:
            for r in all_rows: self.add_preflight(pre,r,'missing_source_file','error','ledger_file 为空，无法定位账套',dry)
            return pre,ref_report
        try:
            if src_path.resolve()==Path(dst).resolve():
                pre.append(audit_row(source_file=src,action='output_path_check',risk_level='error',reason='输出副本路径等于原始账套路径',planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y',output_file=str(dst)))
        except Exception: pass
        if Path(dst).exists():
            pre.append(audit_row(source_file=src,action='output_path_check',risk_level='error',reason='输出目录已存在同名副本，禁止静默覆盖',planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y',output_file=str(dst)))
        for r in all_rows:
            if not sql_value(r.get('old_code','')): self.add_preflight(pre,r,'missing_old_code','error','old_code 为空',dry)
            if not sql_value(r.get('new_code','')): self.add_preflight(pre,r,'missing_new_code','error','new_code 为空',dry)
            if sql_value(r.get('confirmed','')).upper()!='Y': self.add_preflight(pre,r,'unconfirmed_mapping','error','confirmed != Y，commit 默认阻止',dry)
            if sql_value(r.get('action','')).lower()=='create_year_dedicated_child':
                self.add_preflight(pre,r,'unsupported_create_year_dedicated_child','error','当前实现只是重编码，不会 INSERT 创建新子科目；commit 必须阻止',dry)
        by_locator=defaultdict(list)
        for r in all_rows:
            old=sql_value(r.get('old_code',''))
            if old: by_locator[(row_value(r,'source_file_id'),row_value(r,'year'),old)].append(r)
        for key,items in by_locator.items():
            if len(items)>1:
                for r in items: self.add_preflight(pre,r,'duplicate_mapping_locator','error','映射表中同一 source_file_id + year + old_code 出现多行，必须先处理账套内重复或读取重复',dry,duplicate_count=len(items))
        by_new=defaultdict(set)
        for r in all_rows:
            new=sql_value(r.get('new_code','')); old=sql_value(r.get('old_code',''))
            if new and old: by_new[new].add(old)
        for new,olds in by_new.items():
            if len(olds)>1:
                for r in [x for x in all_rows if sql_value(x.get('new_code',''))==new]:
                    self.add_preflight(pre,r,'duplicate_new_code','error','同一账套内多个 old_code 指向同一个 new_code：%s'%(','.join(sorted(olds))),dry)
        try:
            conn=self.connect(src)
            try:
                info=self.ledger_info(conn,src); actual_fid=source_file_id(src); actual_year=row_value(info,'year')
                table,f=self.account_table(conn); cf,nf=f['code'],f['name']; accounts=self.account_map(conn,table,cf,nf); final_codes=self.final_code_set(accounts,all_rows); lengths=code_lengths(final_codes); current_lengths=self.account_level_lengths(conn); target_lengths=sorted(set((current_lengths or [])+lengths)); max_levels=int(self.cfg.get('max_account_levels',6) or 6)
                safe_refs,ref_report=self.reference_fields(conn,table,cf,src,dry)
                if any(is_blocking(x) for x in ref_report):
                    pre.append(audit_row(source_file=src,action='unsafe_reference_fields',risk_level='warning',reason='存在无法确认安全的引用字段；dry-run 仅报告，commit 阻止',planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                if len(target_lengths)>max_levels:
                    pre.append(audit_row(source_file=src,action='account_level_check',risk_level='error',reason='最终科目编码级次需要 %s 个不同长度 %s，超过 max_account_levels=%s'%(len(target_lengths),target_lengths,max_levels),planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                active_lengths=mapping_code_lengths(active_rows); all_lengths=mapping_code_lengths(all_rows)
                if set(all_lengths)-set(active_lengths):
                    pre.append(audit_row(source_file=src,action='account_level_from_skipped_mapping',risk_level='warning',reason='科目级次扩展来自未确认或被跳过映射：%s'%(sorted(set(all_lengths)-set(active_lengths))),planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                for r in all_rows:
                    old=sql_value(r.get('old_code','')); new=sql_value(r.get('new_code','')); parent=sql_value(r.get('new_parent_code','')); action=sql_value(r.get('action','')).lower()
                    if row_value(r,'source_file_id') and row_value(r,'source_file_id')!=actual_fid: self.add_preflight(pre,r,'source_file_id_mismatch','error','映射表 source_file_id 与当前账套不匹配',dry,actual_source_file_id=actual_fid)
                    if row_value(r,'year') and actual_year and row_value(r,'year')!=actual_year: self.add_preflight(pre,r,'year_mismatch','error','映射表年度与当前账套年度不匹配',dry,actual_year=actual_year)
                    if row_value(r,'source_db_table') and row_value(r,'source_db_table')!=table: self.add_preflight(pre,r,'source_table_mismatch','error','映射表来源表与当前账套科目表不匹配',dry,actual_table=table)
                    if row_value(r,'source_code_field') and row_value(r,'source_code_field')!=cf: self.add_preflight(pre,r,'source_code_field_mismatch','error','映射表编码字段与当前账套科目编码字段不匹配',dry,actual_code_field=cf)
                    if old and old not in accounts: self.add_preflight(pre,r,'old_code_missing','error','old_code 在账套科目表中不存在',dry)
                    if new and new in accounts and old!=new and action not in MERGE_ACTIONS: self.add_preflight(pre,r,'new_code_exists_without_merge_action','error','new_code 已存在，但 action 不是明确合并策略',dry)
                    if new and new in accounts and old!=new and action in MERGE_ACTIONS: self.add_preflight(pre,r,'merge_requires_usage_check','error','多个旧科目映射到同一个已存在新科目时，需要先校验余额/凭证是否要合并；当前不会静默 UPDATE',dry)
                    if parent and parent not in final_codes: self.add_preflight(pre,r,'new_parent_missing','error','new_parent_code 在最终科目集合中不存在',dry)
                    if parent and new and not new.startswith(parent): self.add_preflight(pre,r,'parent_prefix_mismatch','error','new_code 不是 new_parent_code 的下级前缀',dry)
                    if new and len(target_lengths)<=max_levels:
                        for implied in required_parent_codes(new,target_lengths):
                            if implied not in final_codes: self.add_preflight(pre,r,'account_level_gap','error','父子级次不连续，缺少上级编码 %s'%implied,dry)
                if not pre:
                    pre.append(audit_row(source_file=src,action='preflight_ok',risk_level='info',reason='未发现阻止 commit 的问题',planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='N'))
            finally: conn.close()
        except Exception as e:
            pre.append(audit_row(source_file=src,action='preflight_connect_or_schema_error',risk_level='error',reason=str(e),planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
        return pre,ref_report
    def apply(self,src,dst,maps,dry=True):
        target=src if dry else dst; audit=[]; conn=None; copied=False
        try:
            if not dry:
                Path(dst).parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst); copied=True
            conn=self.connect(target)
            table,f=self.account_table(conn); cf,nf,pf=f['code'],f['name'],f.get('parent'); cur=conn.cursor(); refs=self.refs(conn,table,cf,src,dry); audit+=self.ensure_account_levels(conn,cur,maps,table,cf,dry)
            for row in audit:
                if not row.get('source_file'): row['source_file']=src
            for m in maps:
                old=m['old_code'].strip(); new=m['new_code'].strip(); name=m['new_name'].strip(); parent=m.get('new_parent_code','').strip()
                if not old or not new: continue
                if old!=new:
                    for t,c in refs:
                        try:
                            cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(t,c),old); cnt=int(cur.fetchone()[0] or 0)
                            audit.append(audit_row(source_file=src,table=t,field=c,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='update_reference',risk_level='info',reason='更新引用表字段',planned_sql_type='UPDATE_REFERENCE',affected_rows=cnt,dry_run=yn(dry),blocked_commit='N',file=src,affected=cnt))
                            if cnt and not dry: cur.execute('UPDATE [%s] SET [%s]=? WHERE [%s]=?'%(t,c,c),new,old)
                        except Exception as e:
                            audit.append(audit_row(source_file=src,table=t,field=c,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='update_reference_error',risk_level='error',reason=str(e),planned_sql_type='UPDATE_REFERENCE',dry_run=yn(dry),blocked_commit='Y',file=src,error=str(e)))
                            if not dry: raise
                try:
                    cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(table,cf),new); exists=int(cur.fetchone()[0] or 0)>0
                    if old==new:
                        cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(table,cf),old); cnt=int(cur.fetchone()[0] or 0)
                        audit.append(audit_row(source_file=src,table=table,field=nf,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='update_account_name',risk_level='info',reason='更新科目名称',planned_sql_type='UPDATE_ACCOUNT_NAME',affected_rows=cnt,dry_run=yn(dry),blocked_commit='N',file=src,code=old))
                        if not dry: cur.execute('UPDATE [%s] SET [%s]=? WHERE [%s]=?'%(table,nf,cf),name,old)
                    elif exists:
                        audit.append(audit_row(source_file=src,table=table,field=cf,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='mapped_to_existing_target',risk_level='warning',reason='引用已改到目标科目，旧科目不自动删除',planned_sql_type='NO_ACCOUNT_UPDATE',affected_rows=0,dry_run=yn(dry),blocked_commit='N',file=src,note='引用已改到目标科目，旧科目不自动删除'))
                    else:
                        cur.execute('SELECT COUNT(*) FROM [%s] WHERE [%s]=?'%(table,cf),old); cnt=int(cur.fetchone()[0] or 0)
                        audit.append(audit_row(source_file=src,table=table,field=cf,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='rename_account',risk_level='info',reason='更新科目编码和名称',planned_sql_type='UPDATE_ACCOUNT',affected_rows=cnt,dry_run=yn(dry),blocked_commit='N',file=src))
                        if not dry:
                            sql='UPDATE [%s] SET [%s]=?, [%s]=?'%(table,cf,nf); vals=[new,name]
                            if pf and parent: sql+=', [%s]=?'%pf; vals.append(parent)
                            sql+=' WHERE [%s]=?'%cf; vals.append(old); cur.execute(sql,vals)
                except Exception as e:
                    audit.append(audit_row(source_file=src,table=table,old_code=old,old_name=m.get('old_name',''),new_code=new,new_name=name,action='account_error',risk_level='error',reason=str(e),planned_sql_type='UPDATE_ACCOUNT',dry_run=yn(dry),blocked_commit='Y',file=src,error=str(e)))
                    if not dry: raise
            conn.rollback() if dry else conn.commit(); return audit
        except Exception as e:
            if conn:
                try: conn.rollback()
                except Exception: pass
            if not dry and copied:
                try:
                    if Path(dst).exists() and Path(dst).resolve()!=Path(src).resolve(): Path(dst).unlink()
                except Exception: pass
            raise ApplyError(str(e),audit) from e
        finally:
            if conn: conn.close()

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

def dedupe_accounts(raw_rows):
    seen={}; out=[]
    for r in raw_rows:
        key=(row_value(r,'ledger_group'),row_value(r,'old_code'),row_value(r,'old_name'))
        if key not in seen:
            seen[key]={**r,'出现账套数':0,'出现年度':'','出现账套文件':''}
            out.append(seen[key])
        item=seen[key]
        years=set([x for x in item.get('出现年度','').split('|') if x]); files=set([x for x in item.get('出现账套文件','').split('|') if x])
        if row_value(r,'year'): years.add(row_value(r,'year'))
        if row_value(r,'source_file'): files.add(row_value(r,'source_file'))
        item['出现年度']='|'.join(sorted(years)); item['出现账套文件']='|'.join(sorted(files)); item['出现账套数']=len(files)
    return out

def conflict_row(kind,risk,reason,rows,**extra):
    first=rows[0] if rows else {}
    return {'check_type':kind,'risk_level':risk,'reason':reason,'账套文件':row_value(first,'source_file','ledger_file'),'账套文件ID':row_value(first,'source_file_id'),'公司名称':row_value(first,'company','company_name'),'年度':row_value(first,'year'),'原始科目编码':row_value(first,'old_code'),'原始科目名称':row_value(first,'old_name'),'source_file':row_value(first,'source_file','ledger_file'),'source_file_id':row_value(first,'source_file_id'),'company':row_value(first,'company','company_name'),'year':row_value(first,'year'),'old_code':row_value(first,'old_code'),'old_name':row_value(first,'old_name'),**extra}

def build_conflicts(raw_rows,summary_rows):
    out=[]
    groups=defaultdict(list)
    for r in raw_rows: groups[(row_value(r,'source_file_id'),row_value(r,'year'),row_value(r,'old_code'))].append(r)
    for key,rows in groups.items():
        if len(rows)>1: out.append(conflict_row('same_file_year_code_duplicate','error','同一账套文件 + 同一年度 + 科目编码重复，账套内科目编码重复或读取重复',rows,duplicate_count=len(rows)))
    groups=defaultdict(list)
    for r in raw_rows: groups[(row_value(r,'source_file_id'),row_value(r,'year'),row_value(r,'old_code'),row_value(r,'old_name'))].append(r)
    for key,rows in groups.items():
        if len(rows)>1: out.append(conflict_row('same_file_year_code_name_duplicate','error','同一账套文件 + 同一年度 + 科目编码 + 科目名称重复，可能重复读取或账套数据异常',rows,duplicate_count=len(rows)))
    by_code=defaultdict(list); by_name=defaultdict(list)
    for r in summary_rows:
        by_code[(row_value(r,'ledger_group'),row_value(r,'old_code'))].append(r)
        by_name[(row_value(r,'ledger_group'),norm(row_value(r,'old_name')))].append(r)
    for key,rows in by_code.items():
        names=sorted(set(row_value(r,'old_name') for r in rows if row_value(r,'old_name')))
        if len(names)>1: out.append(conflict_row('multi_year_same_code_multi_name','high','多年度汇总后，同一个科目编码对应多个科目名称，必须重新编码或人工确认，后续映射必须带年度',rows,names='|'.join(names),ledger_group=key[0]))
    for key,rows in by_name.items():
        codes=sorted(set(row_value(r,'old_code') for r in rows if row_value(r,'old_code')))
        if key[1] and len(codes)>1: out.append(conflict_row('multi_year_same_name_multi_code','needs_mapping','多年度汇总后，同一个科目名称对应多个科目编码，属于标准化场景，需要生成映射建议',rows,codes='|'.join(codes),ledger_group=key[0]))
    return out

def build_mapping_confirmation(raw_rows,summary_rows,cfg):
    code_names=defaultdict(set); name_codes=defaultdict(set); name_rows=defaultdict(list)
    for r in summary_rows:
        group=row_value(r,'ledger_group'); code=row_value(r,'old_code'); name=row_value(r,'old_name')
        if code and name: code_names[(group,code)].add(name)
        if name and code:
            name_codes[(group,norm(name))].add(code); name_rows[(group,norm(name))].append(r)
    canonical={}
    for key,rows in name_rows.items():
        r=sorted(rows,key=lambda x:(row_value(x,'year') or '9999',row_value(x,'old_code')))[0]
        canonical[key]=(row_value(r,'old_code'),row_value(r,'old_name'))
    out=[]
    for r in raw_rows:
        group=row_value(r,'ledger_group'); old=row_value(r,'old_code'); name=row_value(r,'old_name')
        new_code=old; new_name=name; action='keep'; risk='info'; confirmed='Y'; reason=''
        if len(code_names.get((group,old),set()))>1:
            action='needs_recode'; risk='high'; confirmed='N'; reason='同一账套组多年度中，同一个旧编码对应多个名称，必须按年度拆分并人工确认新编码'
        elif len(name_codes.get((group,norm(name)),set()))>1:
            can_code,can_name=canonical.get((group,norm(name)),(old,name))
            if old!=can_code:
                new_code=can_code; new_name=can_name; action='map_to_existing'; risk='needs_mapping'; confirmed='N'; reason='同一科目名称在不同年度使用多个编码，建议映射到较早年度编码，需人工确认'
        lengths=pref_lengths_from_row(r) or code_lengths([row_value(x,'old_code') for x in raw_rows if row_value(x,'ledger_group')==group])
        new_parent=parent_from_code(new_code,lengths) or row_value(r,'old_parent_code')
        row={**r,'旧科目编码':old,'旧科目名称':name,'新科目编码':new_code,'新科目名称':new_name,'处理动作':action,'是否确认':confirmed,'风险级别':risk,'new_code':new_code,'new_name':new_name,'new_parent_code':new_parent,'action':action,'confirmed':confirmed,'risk_level':risk,'reason':reason}
        out.append(row)
    return out

def build_level_plan(mapping_rows,raw_rows,cfg):
    raw_by_file={}
    for r in raw_rows:
        raw_by_file[row_value(r,'source_file_id')]=r
    by_file=defaultdict(list)
    for m in mapping_rows: by_file[row_value(m,'source_file_id')].append(m)
    max_levels=int(cfg.get('max_account_levels',6) or 6); out=[]; conflicts=[]
    for fid,rows in by_file.items():
        raw=raw_by_file.get(fid,rows[0]); codes=sorted(set(row_value(r,'new_code') for r in rows if row_value(r,'new_code')))
        lengths=code_lengths(codes); cur=pref_lengths_from_row(raw); target_lengths=sorted(set((cur or [])+lengths)); target=target_lengths+[0]*(6-len(target_lengths)); cur_pad=cur+[0]*(6-len(cur))
        risk='info'; reason='当前科目级次已支持目标编码'; need='N'
        missing=missing_parent_codes(codes,target_lengths)
        if len(target_lengths)>max_levels:
            risk='error'; reason='新编码级次超过 %s 级：%s'%(max_levels,target_lengths); need='N'
        elif missing:
            risk='error'; reason='新编码父级缺失：%s'%('|'.join(sorted(set(missing)))); need='N'
        elif not cur:
            risk='error'; reason='无法读取当前 GLPref 科目级次，不能确认是否支持目标编码'; need='N'
        elif any(x not in cur for x in lengths):
            risk='needs_level_update'; reason='当前 GLPref 科目级次不支持目标编码，需要先修改 GLPref'; need='Y'
        row={'账套文件':row_value(raw,'source_file','ledger_file'),'账套文件ID':fid,'年度':row_value(raw,'year'),'当前 FAcLevels':sql_value(raw.get('FAcLevels')),'当前 FAcLen1':sql_value(raw.get('FAcLen1')),'当前 FAcLen2':sql_value(raw.get('FAcLen2')),'当前 FAcLen3':sql_value(raw.get('FAcLen3')),'当前 FAcLen4':sql_value(raw.get('FAcLen4')),'当前 FAcLen5':sql_value(raw.get('FAcLen5')),'当前 FAcLen6':sql_value(raw.get('FAcLen6')),'目标 FAcLevels':len(target_lengths),'目标 FAcLen1':target[0] if len(target)>0 else 0,'目标 FAcLen2':target[1] if len(target)>1 else 0,'目标 FAcLen3':target[2] if len(target)>2 else 0,'目标 FAcLen4':target[3] if len(target)>3 else 0,'目标 FAcLen5':target[4] if len(target)>4 else 0,'目标 FAcLen6':target[5] if len(target)>5 else 0,'是否需要修改':need,'风险级别':risk,'原因':reason,'source_file':row_value(raw,'source_file','ledger_file'),'source_file_id':fid,'year':row_value(raw,'year'),'risk_level':risk,'reason':reason}
        out.append(row)
        if risk in ('error','needs_level_update'): conflicts.append(conflict_row('account_level_check',risk,reason,[raw],source_file_id=fid))
    return out,conflicts

def build_standard_accounts(mapping_rows):
    seen={}; out=[]
    for m in mapping_rows:
        key=(row_value(m,'ledger_group'),row_value(m,'new_code'),row_value(m,'new_name'))
        if key in seen: continue
        row={'账套组':key[0],'科目编码':key[1],'科目名称':key[2],'上级科目编码':row_value(m,'new_parent_code'),'ledger_group':key[0],'new_code':key[1],'new_name':key[2],'new_parent_code':row_value(m,'new_parent_code')}
        seen[key]=row; out.append(row)
    return sorted(out,key=lambda r:(row_value(r,'ledger_group'),row_value(r,'new_code')))

def cmd_make_config(a):
    with open(a.out,'w',encoding='utf-8') as f: json.dump(DEFAULT_CONFIG,f,ensure_ascii=False,indent=2)
    print('已生成配置：',a.out)
def cmd_inspect(a):
    cfg=load_config_for_args(a); db=AccessDB(cfg); accounts=[]; schemas=[]; errs=[]
    for f in ledgers(a.input):
        print('读取',f)
        try:
            x,s=db.read_accounts(f); accounts+=x; schemas+=s
        except Exception as e: errs.append({'账套文件':str(f),'错误':str(e)})
    out=Path(a.out); write_csv(out/'01_账套表结构清单.csv',schemas); write_csv(out/'02_多年科目汇总.csv',accounts)
    if errs: write_csv(out/'00_读取错误.csv',errs)
    maps,std,ex=build_plan(accounts,cfg); write_csv(out/'03_标准科目表_草稿.csv',std); write_csv(out/'04_科目旧新映射_草稿.csv',maps); write_csv(out/'05_冲突和需人工确认清单.csv',ex)
    print('完成，输出目录：',out)
def cmd_scan_kis(a):
    cfg=load_config_for_args(a); db=AccessDB(cfg); accounts=[]; aux=[]; errs=[]; perf=[]; out=Path(a.out)
    for f in ledgers(a.input):
        print('快速扫描',f)
        try:
            x,y,e,p=db.scan_kis_file(f); accounts+=x; aux+=y; errs+=e; perf.append(p)
        except Exception as e:
            pa=getattr(e,'audit',[])
            if pa: perf+=pa
            errs.append({'source_file':str(f),'source_file_id':source_file_id(f),'ledger_file':str(f),'stage':'connect_or_scan','error':str(e)})
    summary=dedupe_accounts(accounts)
    conflicts=build_conflicts(accounts,summary)
    maps=build_mapping_confirmation(accounts,summary,cfg)
    level_plan,level_conflicts=build_level_plan(maps,accounts,cfg)
    conflicts+=level_conflicts
    std=build_standard_accounts(maps)
    raw_cols=['账套文件','账套文件ID','公司名称','年度','原始科目编码','原始科目名称','父级编码','级次','来源表','编码字段','名称字段','source_file','source_file_id','company','year','old_code','old_name','old_parent_code','level','source_db_table','source_code_field','source_name_field','ledger_group','FAcLevels','FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6']
    summary_cols=['账套组','科目编码','科目名称','父级编码','出现年度','出现账套数','出现账套文件','ledger_group','old_code','old_name','old_parent_code']
    for r in summary:
        r.setdefault('账套组',row_value(r,'ledger_group')); r.setdefault('科目编码',row_value(r,'old_code')); r.setdefault('科目名称',row_value(r,'old_name')); r.setdefault('父级编码',row_value(r,'old_parent_code'))
    conflict_cols=['check_type','risk_level','reason','账套文件','账套文件ID','公司名称','年度','原始科目编码','原始科目名称','source_file','source_file_id','company','year','old_code','old_name','duplicate_count','names','codes','ledger_group']
    map_cols=['账套文件','账套文件ID','公司名称','年度','旧科目编码','旧科目名称','新科目编码','新科目名称','处理动作','是否确认','风险级别','来源表','编码字段','名称字段','source_file','source_file_id','company','year','old_code','old_name','new_code','new_name','new_parent_code','action','confirmed','risk_level','source_db_table','source_code_field','source_name_field','ledger_group','reason']
    for m in maps:
        m.setdefault('账套文件',row_value(m,'source_file')); m.setdefault('账套文件ID',row_value(m,'source_file_id')); m.setdefault('公司名称',row_value(m,'company')); m.setdefault('年度',row_value(m,'year')); m.setdefault('来源表',row_value(m,'source_db_table')); m.setdefault('编码字段',row_value(m,'source_code_field')); m.setdefault('名称字段',row_value(m,'source_name_field'))
    level_cols=['账套文件','账套文件ID','年度','当前 FAcLevels','当前 FAcLen1','当前 FAcLen2','当前 FAcLen3','当前 FAcLen4','当前 FAcLen5','当前 FAcLen6','目标 FAcLevels','目标 FAcLen1','目标 FAcLen2','目标 FAcLen3','目标 FAcLen4','目标 FAcLen5','目标 FAcLen6','是否需要修改','风险级别','原因','source_file','source_file_id','year','risk_level','reason']
    aux_cols=['账套文件','账套文件ID','公司名称','年度','核算项目来源表','item_code','item_name','item_type','account_code','source_file','source_file_id','company','year','source_table','ledger_group']
    for r in aux:
        r.setdefault('账套文件',row_value(r,'source_file')); r.setdefault('账套文件ID',row_value(r,'source_file_id')); r.setdefault('公司名称',row_value(r,'company')); r.setdefault('年度',row_value(r,'year'))
    err_cols=['账套文件','账套文件ID','阶段','错误','source_file','source_file_id','ledger_file','stage','error']
    for e in errs:
        e.setdefault('账套文件',row_value(e,'source_file','ledger_file')); e.setdefault('账套文件ID',row_value(e,'source_file_id')); e.setdefault('阶段',row_value(e,'stage')); e.setdefault('错误',row_value(e,'error'))
    perf_cols=['账套文件','账套文件ID','状态','耗时毫秒','读取科目数','读取核算项目数','读取表','source_file','source_file_id','ledger_file','ledger_name','scan_mode','connected','status','elapsed_ms','account_rows','auxiliary_rows','touched_tables','full_table_scan','full_field_scan','refs_called','write_sql','error']
    for p in perf:
        p.setdefault('账套文件',row_value(p,'source_file','ledger_file')); p.setdefault('账套文件ID',row_value(p,'source_file_id')); p.setdefault('状态',row_value(p,'status')); p.setdefault('耗时毫秒',row_value(p,'elapsed_ms')); p.setdefault('读取科目数',row_value(p,'account_rows')); p.setdefault('读取核算项目数',row_value(p,'auxiliary_rows')); p.setdefault('读取表',row_value(p,'touched_tables'))
    write_csv_columns(out/'01_账套科目原始明细.csv',accounts,raw_cols)
    write_csv_columns(out/'02_多年科目汇总_去重.csv',summary,summary_cols)
    write_csv_columns(out/'03_科目编码冲突检查.csv',conflicts,conflict_cols)
    write_csv_columns(out/'04_科目映射确认表.csv',maps,map_cols)
    write_csv_columns(out/'05_科目级次修改计划.csv',level_plan,level_cols)
    write_csv_columns(out/'06_核算项目汇总.csv',aux,aux_cols)
    write_csv_columns(out/'07_扫描错误报告.csv',errs,err_cols)
    write_csv_columns(out/'08_扫描性能统计.csv',perf,perf_cols)
    write_csv_columns(out/'09_账套修改审计.csv',[],['source_file','source_file_id','year','table','field','old_code','new_code','action','risk_level','reason','affected_rows','dry_run','blocked_commit'])
    print('完成，输出目录：',out)
def cmd_apply(a):
    cfg=load_config_for_args(a); db=AccessDB(cfg); rows=[normalize_mapping_row(r) for r in read_csv(a.mapping)]; by=defaultdict(list); all_by=defaultdict(list); skipped=[]; out=Path(a.out); audit=[]; preflight=[]; ref_report=[]
    cfg['_target_account_lengths']=[]
    for r in rows:
        src=row_value(r,'source_file','ledger_file'); all_by[src].append(r)
        if not a.allow_unconfirmed and str(r.get('confirmed','')).upper()!='Y': skipped.append(r); continue
        by[src].append(r)
    source_years={}
    for src,items in all_by.items():
        for r in items:
            y=sql_value(r.get('year',''))
            if y:
                source_years[src]=y; break
    dst_by=ledger_output_paths(out,[s for s in all_by.keys() if s],source_years)
    for src,all_rows in all_by.items():
        dst=dst_by.get(src,'') if src else ''
        pf,rr=db.preflight_apply(src,dst,by.get(src,[]),all_rows,a.dry_run); preflight+=pf; ref_report+=rr
    write_csv(out/'preflight_report.csv',preflight); write_csv(out/'reference_fields_report.csv',ref_report)
    blocked=[r for r in preflight+ref_report if is_blocking(r)]
    if blocked and not a.dry_run:
        audit.append(audit_row(action='commit_blocked',risk_level='error',reason='preflight_report.csv 或 reference_fields_report.csv 存在阻断项，未复制/未写入账套',planned_sql_type='NONE',dry_run='N',blocked_commit='Y',affected_rows=0,blocked_count=len(blocked)))
        write_csv(out/'apply_audit_commit.csv',audit)
        write_csv(out/'09_账套修改审计.csv',audit)
        if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
        print('commit 已阻止：请先查看 preflight_report.csv 和 reference_fields_report.csv')
        raise SystemExit(1)
    for src,maps in by.items():
        if not src: continue
        print(('试运行 ' if a.dry_run else '写入副本 ')+src)
        try: audit+=db.apply(src,dst_by.get(src,str(out/Path(src).name)),maps,a.dry_run)
        except Exception as e:
            audit+=getattr(e,'audit',[])
            audit.append(audit_row(source_file=src,action='apply_error',risk_level='error',reason=str(e),planned_sql_type='APPLY',dry_run=yn(a.dry_run),blocked_commit='Y',file=src,error=str(e)))
            if not a.dry_run:
                write_csv(out/'apply_audit_commit.csv',audit)
                write_csv(out/'09_账套修改审计.csv',audit)
                if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
                print('commit 写入失败，已 rollback：',e)
                raise SystemExit(1)
    write_csv(out/('apply_audit_dryrun.csv' if a.dry_run else 'apply_audit_commit.csv'),audit)
    write_csv(out/'09_账套修改审计.csv',audit)
    if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
    print('完成。')
def main():
    p=argparse.ArgumentParser(description='金蝶 KIS 多年账套科目标准化工具')
    sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('make-config'); q.add_argument('--out',required=True); q.set_defaults(func=cmd_make_config)
    q=sub.add_parser('inspect'); q.add_argument('--input',required=True); q.add_argument('--out',required=True); q.add_argument('--config'); q.add_argument('--systemdb'); q.set_defaults(func=cmd_inspect)
    q=sub.add_parser('scan-kis'); q.add_argument('--input',required=True); q.add_argument('--out',required=True); q.add_argument('--config'); q.add_argument('--systemdb'); q.set_defaults(func=cmd_scan_kis)
    q=sub.add_parser('apply'); q.add_argument('--mapping',required=True); q.add_argument('--out',required=True); q.add_argument('--config'); q.add_argument('--systemdb'); q.add_argument('--dry-run',action='store_true',default=True); q.add_argument('--commit',dest='dry_run',action='store_false'); q.add_argument('--allow-unconfirmed',action='store_true'); q.set_defaults(func=cmd_apply)
    a=p.parse_args(); a.func(a)
if __name__=='__main__': main()
