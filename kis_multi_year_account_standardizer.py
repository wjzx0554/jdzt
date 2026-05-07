# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, csv, json, os, re, shutil, tempfile, platform, sys, time
from pathlib import Path
from collections import defaultdict

LEDGER_SUFFIXES={'.ais','.aiy','.axx','.mdb'}
DEFAULT_CONFIG={
 'account_table_candidates':['GLAcct','t_Account','Account','Acct'],
 'account_code_candidates':['FAcctID','FNumber','FCode','AcctCode','AccountCode','科目编码'],
 'account_name_candidates':['FName','FAcctName','FAccountName','FDetailName','科目名称'],
 'account_fullname_candidates':['FFullName','FullName','科目全名'],
 'parent_code_candidates':['FParentID','FParentCode','ParentCode','上级科目编码'],
 'known_reference_fields':{'GLBal':['FAcctID','FNumber','FCode'],'GLBalHist':['FAcctID','FNumber','FCode'],'GLInitBal':['FAcctID','FNumber','FCode'],'GLInitData':['FAcctID'],'GLVch':['FAcctID','FNumber','FCode'],'GLVchEntry':['FAcctID','FNumber','FCode'],'GLObj':['FAcctID','FNumber','FCode'],'GLCash':['FAcctID','FNumber','FCode'],'GLMultiBal':['FAcctID','FNumber','FCode']},
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

def code_lengths(codes):
    return sorted(set([len(sql_value(c)) for c in codes if sql_value(c)]))

def mapping_code_lengths(rows):
    codes=[]
    for r in rows:
        for k in ['new_code','new_parent_code','old_code','old_parent_code']:
            v=sql_value(r.get(k,''))
            if v: codes.append(v)
    return code_lengths(codes)

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
        perf={'ledger_file':str(file),'ledger_name':Path(file).stem,'scan_mode':'scan-kis','connected':'N','full_table_scan':'N','full_field_scan':'N','refs_called':'N','write_sql':'N','status':'error'}
        try:
            conn=self.connect(str(file)); perf['connected']='Y'
            pref={}; pref_fields=[]
            pref_candidates=['FCompany','FStartYear','FStartPeriod','FCurrYear','FCurrPeriod','FNaturalStartYear','FAcLevels','FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6']
            if self.fast_table_exists(conn,'GLPref'):
                pref,pref_fields=self.fast_one_known(conn,'GLPref',pref_candidates); touched.append('GLPref')
            else: errors.append({'ledger_file':str(file),'stage':'GLPref','error':'GLPref 不存在'})
            vch={}
            if self.fast_table_exists(conn,'GLVch'):
                try: vch=self.fast_voucher_bounds(conn); touched.append('GLVch(MIN/MAX)')
                except Exception as e: errors.append({'ledger_file':str(file),'stage':'GLVch','error':str(e)})
            info={'ledger_file':str(file),'ledger_name':Path(file).stem,'company_name':sql_value(pref.get('FCompany')),'start_year':year_from_value(pref.get('FStartYear')),'current_year':year_from_value(pref.get('FCurrYear')),'start_period':sql_value(pref.get('FStartPeriod')),'current_period':sql_value(pref.get('FCurrPeriod')),'natural_start_year':year_from_value(pref.get('FNaturalStartYear')),'voucher_start_date':sql_value(vch.get('voucher_start_date')),'voucher_end_date':sql_value(vch.get('voucher_end_date')),'voucher_min_year':sql_value(vch.get('voucher_min_year')),'voucher_max_year':sql_value(vch.get('voucher_max_year')),'voucher_min_period':sql_value(vch.get('voucher_min_period')),'voucher_max_period':sql_value(vch.get('voucher_max_period')),'FAcLevels':sql_value(pref.get('FAcLevels')),'FAcLen1':sql_value(pref.get('FAcLen1')),'FAcLen2':sql_value(pref.get('FAcLen2')),'FAcLen3':sql_value(pref.get('FAcLen3')),'FAcLen4':sql_value(pref.get('FAcLen4')),'FAcLen5':sql_value(pref.get('FAcLen5')),'FAcLen6':sql_value(pref.get('FAcLen6'))}
            for y in [info.get('current_year'),info.get('start_year'),year_from_value(info.get('voucher_max_year')),year_from_value(info.get('voucher_end_date')),year_of(file)]:
                if y: info['year']=y; break
            if 'year' not in info: info['year']=''
            level_lengths=sorted(set([int(info[k]) for k in ['FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6'] if sql_value(info.get(k)).isdigit() and int(info[k])>0]))
            if self.fast_table_exists(conn,'GLAcct'):
                touched.append('GLAcct')
                cf=self.fast_pick_field(conn,'GLAcct',['FAcctID','FNumber','FCode','AcctCode','AccountCode'])
                nf=self.fast_pick_field(conn,'GLAcct',['FName','FAcctName','FAccountName','FDetailName'])
                ff=self.fast_pick_field(conn,'GLAcct',['FFullName','FullName'])
                pf=self.fast_pick_field(conn,'GLAcct',['FParentID','FParentCode','ParentCode'])
                if not cf or not nf:
                    errors.append({'ledger_file':str(file),'stage':'GLAcct','error':'GLAcct 无法识别科目编码/名称字段'})
                else:
                    fields=[x for x in [cf,nf,ff,pf] if x]; rows,fields=self.fast_rows_known(conn,'GLAcct',fields); codes=[self.first_of(r,[cf]) for r in rows if self.first_of(r,[cf])]; lengths=level_lengths or code_lengths(codes)
                    for r in rows:
                        code=self.first_of(r,[cf]); name=self.first_of(r,[nf])
                        if not code: continue
                        accounts.append({**info,'account_table':'GLAcct','account_code_field':cf,'account_name_field':nf,'old_code':code,'old_name':name,'old_full_name':self.first_of(r,[ff]) if ff else '','old_parent_code':self.first_of(r,[pf]) if pf else parent_from_code(code,lengths),'used_in_voucher':'','used_in_balance':'','voucher_usage_checked':'N','balance_usage_checked':'N'})
            else: errors.append({'ledger_file':str(file),'stage':'GLAcct','error':'GLAcct 不存在'})
            aux_specs={
                'GLObj':['FObjID','FObjectID','FNumber','FCode','FAcctID','FName','FObjName','FItemName','FClassID','FType','FGroupID'],
                'GLEmp':['FEmpID','FEmpCode','FNumber','FCode','FName','FEmpName','FDeptID','FGroupID'],
                'PAData':['FDataID','FItemID','FNumber','FCode','FAcctID','FName','FDataName','FItemName','FClassID','FType'],
                'PAItem':['FItemID','FNumber','FCode','FAcctID','FName','FItemName','FClassID','FType']
            }
            code_cands=['FObjID','FObjectID','FEmpID','FEmpCode','FDataID','FItemID','FNumber','FCode']
            name_cands=['FName','FObjName','FEmpName','FDataName','FItemName']
            type_cands=['FClassID','FType','FGroupID']
            for t,cands in aux_specs.items():
                if not self.fast_table_exists(conn,t): continue
                touched.append(t)
                try:
                    rows,fields=self.fast_rows_known(conn,t,cands)
                    for r in rows:
                        aux.append({**info,'source_table':t,'item_code':self.first_of(r,code_cands),'item_name':self.first_of(r,name_cands),'item_type':self.first_of(r,type_cands),'account_code':self.first_of(r,['FAcctID'])})
                except Exception as e: errors.append({'ledger_file':str(file),'stage':t,'error':str(e)})
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
        max_levels=int(self.cfg.get('max_account_levels',6) or 6)
        if len(desired)>max_levels:
            msg='目标科目编码出现 %s 个不同长度：%s；KIS 迷你版通常最多支持 %s 级，请先调整映射编码。'%(len(desired),desired,max_levels)
            audit.append(audit_row(table='GLPref',action='account_level_error',risk_level='error',reason=msg,planned_sql_type='UPDATE_GLPREF',dry_run=yn(dry),blocked_commit='Y'))
            if not dry: raise RuntimeError(msg)
            return audit
        old_pref=self.account_level_pref(conn); old=self.account_code_lengths(conn); padded=desired+[0]*(6-len(desired))
        audit.append(audit_row(table='GLPref',action='account_level_plan',risk_level='info',reason='更新 GLPref 科目级次，支持后续下级科目或字母编码',planned_sql_type='UPDATE_GLPREF',affected_rows=1 if old!=desired else 0,dry_run=yn(dry),blocked_commit='N',old_FAcLevels=sql_value(old_pref.get('FAcLevels')),old_FAcLen1=sql_value(old_pref.get('FAcLen1')),old_FAcLen2=sql_value(old_pref.get('FAcLen2')),old_FAcLen3=sql_value(old_pref.get('FAcLen3')),old_FAcLen4=sql_value(old_pref.get('FAcLen4')),old_FAcLen5=sql_value(old_pref.get('FAcLen5')),old_FAcLen6=sql_value(old_pref.get('FAcLen6')),new_FAcLevels=len(desired),new_FAcLen1=padded[0],new_FAcLen2=padded[1],new_FAcLen3=padded[2],new_FAcLen4=padded[3],new_FAcLen5=padded[4],new_FAcLen6=padded[5],old_lengths=','.join(map(str,old)),new_lengths=','.join(map(str,desired))))
        if old==desired: return audit
        if not dry:
            cur.execute('UPDATE [GLPref] SET [FAcLevels]=?, [FAcLen1]=?, [FAcLen2]=?, [FAcLen3]=?, [FAcLen4]=?, [FAcLen5]=?, [FAcLen6]=?',len(desired),padded[0],padded[1],padded[2],padded[3],padded[4],padded[5])
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
        auto=list(self.cfg.get('auto_reference_field_names') or [])
        for t in self.tables(conn):
            try: cs=self.cols(conn,t)
            except Exception as e:
                report.append(audit_row(source_file=source_file,table=t,action='reference_field_scan_error',risk_level='error',reason=str(e),dry_run=yn(dry),blocked_commit='Y'))
                continue
            candidates=[]
            for c in list(known.get(t,[]))+auto:
                if c in cs and c not in candidates: candidates.append(c)
            for c in candidates:
                if t==account_table:
                    report.append(audit_row(source_file=source_file,table=t,field=c,action='reference_field_excluded',risk_level='info',reason='科目表字段由独立科目更新逻辑处理，不作为普通引用字段更新',planned_sql_type='NONE',dry_run=yn(dry),blocked_commit='N',safe='N'))
                elif t in known and c in known.get(t,[]):
                    refs.append((t,c))
                    report.append(audit_row(source_file=source_file,table=t,field=c,action='reference_field_safe',risk_level='info',reason='字段在 known_reference_fields 中，允许作为引用字段更新',planned_sql_type='UPDATE_REFERENCE',dry_run=yn(dry),blocked_commit='N',safe='Y'))
                else:
                    report.append(audit_row(source_file=source_file,table=t,field=c,action='reference_field_uncertain',risk_level='warning',reason='字段名命中自动候选，但表未显式列入 known_reference_fields，无法确认安全；commit 默认阻止',planned_sql_type='UPDATE_REFERENCE',dry_run=yn(dry),blocked_commit='Y',safe='N'))
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
        out.append(audit_row(source_file=sql_value(row.get('ledger_file','')),old_code=sql_value(row.get('old_code','')),old_name=sql_value(row.get('old_name','')),new_code=sql_value(row.get('new_code','')),new_name=sql_value(row.get('new_name','')),action=sql_value(row.get('action','')),risk_level=risk,reason=reason,planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit=yn(blocked),check=check,confirmed=sql_value(row.get('confirmed','')),new_parent_code=sql_value(row.get('new_parent_code','')),**extra))
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
                table,f=self.account_table(conn); cf,nf=f['code'],f['name']; accounts=self.account_map(conn,table,cf,nf); final_codes=self.final_code_set(accounts,all_rows); lengths=code_lengths(final_codes); max_levels=int(self.cfg.get('max_account_levels',6) or 6)
                safe_refs,ref_report=self.reference_fields(conn,table,cf,src,dry)
                if any(is_blocking(x) for x in ref_report):
                    pre.append(audit_row(source_file=src,action='unsafe_reference_fields',risk_level='warning',reason='存在无法确认安全的引用字段；dry-run 仅报告，commit 阻止',planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                if len(lengths)>max_levels:
                    pre.append(audit_row(source_file=src,action='account_level_check',risk_level='error',reason='最终科目编码出现 %s 个不同长度 %s，超过 max_account_levels=%s'%(len(lengths),lengths,max_levels),planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                active_lengths=mapping_code_lengths(active_rows); all_lengths=mapping_code_lengths(all_rows)
                if set(all_lengths)-set(active_lengths):
                    pre.append(audit_row(source_file=src,action='account_level_from_skipped_mapping',risk_level='warning',reason='科目级次扩展来自未确认或被跳过映射：%s'%(sorted(set(all_lengths)-set(active_lengths))),planned_sql_type='PREFLIGHT',dry_run=yn(dry),blocked_commit='Y'))
                for r in all_rows:
                    old=sql_value(r.get('old_code','')); new=sql_value(r.get('new_code','')); parent=sql_value(r.get('new_parent_code','')); action=sql_value(r.get('action','')).lower()
                    if old and old not in accounts: self.add_preflight(pre,r,'old_code_missing','error','old_code 在账套科目表中不存在',dry)
                    if new and new in accounts and old!=new and action not in MERGE_ACTIONS: self.add_preflight(pre,r,'new_code_exists_without_merge_action','error','new_code 已存在，但 action 不是明确合并策略',dry)
                    if parent and parent not in final_codes: self.add_preflight(pre,r,'new_parent_missing','error','new_parent_code 在最终科目集合中不存在',dry)
                    if parent and new and not new.startswith(parent): self.add_preflight(pre,r,'parent_prefix_mismatch','error','new_code 不是 new_parent_code 的下级前缀',dry)
                    if new and len(lengths)<=max_levels:
                        smaller=[x for x in lengths if x<len(new)]
                        if smaller:
                            implied=new[:max(smaller)]
                            if implied not in final_codes: self.add_preflight(pre,r,'account_level_gap','error','父子级次不连续，缺少直接上级编码 %s'%implied,dry)
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
            errs.append({'ledger_file':str(f),'stage':'connect_or_scan','error':str(e)})
    maps,std,ex=build_plan(accounts,cfg) if accounts else ([],[],[])
    account_cols=['ledger_file','ledger_name','year','company_name','start_year','current_year','start_period','current_period','natural_start_year','voucher_start_date','voucher_end_date','voucher_min_year','voucher_max_year','voucher_min_period','voucher_max_period','FAcLevels','FAcLen1','FAcLen2','FAcLen3','FAcLen4','FAcLen5','FAcLen6','account_table','account_code_field','account_name_field','old_code','old_name','old_full_name','old_parent_code','used_in_voucher','used_in_balance','voucher_usage_checked','balance_usage_checked']
    aux_cols=['ledger_file','ledger_name','year','company_name','source_table','item_code','item_name','item_type','account_code']
    err_cols=['ledger_file','stage','error']
    perf_cols=['ledger_file','ledger_name','scan_mode','connected','status','elapsed_ms','account_rows','auxiliary_rows','touched_tables','full_table_scan','full_field_scan','refs_called','write_sql','error']
    map_cols=['ledger_file','ledger_name','year','old_code','old_name','old_full_name','old_parent_code','used_in_voucher','used_in_balance','new_code','new_name','new_parent_code','action','conflict_type','reason','confirmed']
    write_csv_columns(out/'kis_accounts_summary.csv',accounts,account_cols)
    write_csv_columns(out/'kis_auxiliary_items.csv',aux,aux_cols)
    write_csv_columns(out/'kis_scan_errors.csv',errs,err_cols)
    write_csv_columns(out/'kis_scan_performance.csv',perf,perf_cols)
    write_csv_columns(out/'standard_accounts_draft.csv',std,['科目编码','科目名称','上级科目编码'])
    write_csv_columns(out/'mapping_draft.csv',maps,map_cols)
    if ex: write_csv(out/'kis_scan_conflicts.csv',ex)
    print('完成，输出目录：',out)
def cmd_apply(a):
    cfg=load_config_for_args(a); db=AccessDB(cfg); rows=read_csv(a.mapping); by=defaultdict(list); all_by=defaultdict(list); skipped=[]; out=Path(a.out); audit=[]; preflight=[]; ref_report=[]
    cfg['_target_account_lengths']=mapping_code_lengths(rows)
    for r in rows:
        src=sql_value(r.get('ledger_file','')); all_by[src].append(r)
        if not a.allow_unconfirmed and str(r.get('confirmed','')).upper()!='Y': skipped.append(r); continue
        by[src].append(r)
    for src,all_rows in all_by.items():
        dst=str(out/Path(src).name) if src else ''
        pf,rr=db.preflight_apply(src,dst,by.get(src,[]),all_rows,a.dry_run); preflight+=pf; ref_report+=rr
    write_csv(out/'preflight_report.csv',preflight); write_csv(out/'reference_fields_report.csv',ref_report)
    blocked=[r for r in preflight+ref_report if is_blocking(r)]
    if blocked and not a.dry_run:
        audit.append(audit_row(action='commit_blocked',risk_level='error',reason='preflight_report.csv 或 reference_fields_report.csv 存在阻断项，未复制/未写入账套',planned_sql_type='NONE',dry_run='N',blocked_commit='Y',affected_rows=0,blocked_count=len(blocked)))
        write_csv(out/'apply_audit_commit.csv',audit)
        if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
        print('commit 已阻止：请先查看 preflight_report.csv 和 reference_fields_report.csv')
        raise SystemExit(1)
    for src,maps in by.items():
        if not src: continue
        print(('试运行 ' if a.dry_run else '写入副本 ')+src)
        try: audit+=db.apply(src,str(out/Path(src).name),maps,a.dry_run)
        except Exception as e:
            audit+=getattr(e,'audit',[])
            audit.append(audit_row(source_file=src,action='apply_error',risk_level='error',reason=str(e),planned_sql_type='APPLY',dry_run=yn(a.dry_run),blocked_commit='Y',file=src,error=str(e)))
            if not a.dry_run:
                write_csv(out/'apply_audit_commit.csv',audit)
                if skipped: write_csv(out/'skipped_unconfirmed_mapping.csv',skipped)
                print('commit 写入失败，已 rollback：',e)
                raise SystemExit(1)
    write_csv(out/('apply_audit_dryrun.csv' if a.dry_run else 'apply_audit_commit.csv'),audit)
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
