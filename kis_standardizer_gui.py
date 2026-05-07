# -*- coding: utf-8 -*-
from __future__ import annotations
import contextlib, queue, sys, threading, traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import kis_multi_year_account_standardizer as core

APP_TITLE='金蝶多年账套科目标准化工具'
class TextRedirector:
    def __init__(self,q): self.q=q
    def write(self,s):
        if s: self.q.put(s)
    def flush(self): pass
class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(APP_TITLE); self.geometry('980x700'); self.minsize(900,620)
        self.log_queue=queue.Queue(); self.input_dir=tk.StringVar(); self.work_dir=tk.StringVar(); self.output_dir=tk.StringVar(); self.config_file=tk.StringVar(); self.mapping_file=tk.StringVar(); self.allow_unconfirmed=tk.BooleanVar(value=False)
        self.build(); self.after(100,self.poll)
    def build(self):
        root=ttk.Frame(self,padding=12); root.pack(fill=tk.BOTH,expand=True)
        ttk.Label(root,text=APP_TITLE,font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(root,text='读取多年 AIS/AIY/AXX 账套，生成统一科目映射；确认后修改各年度账套副本，供云会计多年合并升级。',wraplength=900).pack(anchor='w',pady=(4,12))
        frm=ttk.LabelFrame(root,text='路径设置',padding=10); frm.pack(fill=tk.X)
        self.row(frm,0,'原始多年账套目录',self.input_dir,'dir'); self.row(frm,1,'加工区/映射输出目录',self.work_dir,'dir'); self.row(frm,2,'处理后账套副本目录',self.output_dir,'dir'); self.row(frm,3,'配置文件 config.json',self.config_file,'save'); self.row(frm,4,'科目旧新映射 CSV',self.mapping_file,'file')
        ttk.Label(frm,text='流程：生成配置 → 读取账套生成映射草稿 → 人工复核 CSV → 试运行 → 正式生成账套副本',foreground='#555').grid(row=5,column=0,columnspan=3,sticky='w',pady=(8,0))
        btn=ttk.Frame(root); btn.pack(fill=tk.X,pady=12)
        ttk.Button(btn,text='① 生成配置文件',command=self.make_config).pack(side=tk.LEFT,padx=(0,8)); ttk.Button(btn,text='② 读取账套/生成映射草稿',command=self.inspect).pack(side=tk.LEFT,padx=(0,8)); ttk.Button(btn,text='③ 试运行修改',command=self.apply_dry).pack(side=tk.LEFT,padx=(0,8)); ttk.Button(btn,text='④ 正式生成账套副本',command=self.apply_commit).pack(side=tk.LEFT,padx=(0,8)); ttk.Button(btn,text='清空日志',command=lambda:self.log.delete('1.0',tk.END)).pack(side=tk.RIGHT)
        opts=ttk.Frame(root); opts.pack(fill=tk.X,pady=(0,8)); ttk.Checkbutton(opts,text='允许处理未确认映射（不建议）',variable=self.allow_unconfirmed).pack(side=tk.LEFT)
        lf=ttk.LabelFrame(root,text='运行日志',padding=8); lf.pack(fill=tk.BOTH,expand=True)
        self.log=tk.Text(lf,height=22,wrap='word'); self.log.pack(side=tk.LEFT,fill=tk.BOTH,expand=True); sb=ttk.Scrollbar(lf,command=self.log.yview); sb.pack(side=tk.RIGHT,fill=tk.Y); self.log.config(yscrollcommand=sb.set)
        self.out('准备就绪。正式处理前请备份原始账套。\n\n')
    def row(self,parent,row,label,var,kind):
        ttk.Label(parent,text=label,width=22).grid(row=row,column=0,sticky='w',pady=4); ttk.Entry(parent,textvariable=var).grid(row=row,column=1,sticky='ew',padx=8,pady=4); parent.columnconfigure(1,weight=1); ttk.Button(parent,text='选择',command=lambda:self.choose(var,kind)).grid(row=row,column=2,pady=4)
    def choose(self,var,kind):
        if kind=='dir': p=filedialog.askdirectory()
        elif kind=='file': p=filedialog.askopenfilename(filetypes=[('CSV 文件','*.csv'),('所有文件','*.*')])
        else: p=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('JSON 文件','*.json'),('所有文件','*.*')],initialfile='config.json')
        if p: var.set(p)
    def out(self,s): self.log.insert(tk.END,s); self.log.see(tk.END)
    def poll(self):
        try:
            while True: self.out(self.log_queue.get_nowait())
        except queue.Empty: pass
        self.after(100,self.poll)
    def require(self,*items):
        miss=[n for n,v in items if not v.get().strip()]
        if miss: messagebox.showwarning('缺少路径','请先填写：'+'、'.join(miss)); return False
        return True
    def run_core(self,argv,done='完成。'):
        def task():
            old=sys.argv[:]; self.log_queue.put('\n>>> '+' '.join(argv)+'\n')
            try:
                sys.argv=argv[:]; r=TextRedirector(self.log_queue)
                with contextlib.redirect_stdout(r), contextlib.redirect_stderr(r): core.main()
                self.log_queue.put(done+'\n')
            except SystemExit as e:
                if e.code not in (0,None): self.log_queue.put('运行结束，返回码：%s\n'%e.code)
            except Exception: self.log_queue.put('\n发生错误：\n'+traceback.format_exc())
            finally: sys.argv=old
        threading.Thread(target=task,daemon=True).start()
    def make_config(self):
        if not self.config_file.get().strip(): self.config_file.set(str(Path.cwd()/'config.json'))
        self.run_core(['kis_multi_year_account_standardizer.py','make-config','--out',self.config_file.get()],'配置文件已生成。')
    def inspect(self):
        if not self.require(('原始多年账套目录',self.input_dir),('加工区/映射输出目录',self.work_dir)): return
        argv=['kis_multi_year_account_standardizer.py','inspect','--input',self.input_dir.get(),'--out',self.work_dir.get()]
        if self.config_file.get().strip(): argv+=['--config',self.config_file.get()]
        self.mapping_file.set(str(Path(self.work_dir.get())/'04_科目旧新映射_草稿.csv')); self.run_core(argv,'读取完成。请复核映射 CSV。')
    def apply_dry(self):
        if not self.require(('科目旧新映射 CSV',self.mapping_file),('处理后账套副本目录',self.output_dir)): return
        argv=['kis_multi_year_account_standardizer.py','apply','--mapping',self.mapping_file.get(),'--out',self.output_dir.get()]
        if self.config_file.get().strip(): argv+=['--config',self.config_file.get()]
        if self.allow_unconfirmed.get(): argv+=['--allow-unconfirmed']
        self.run_core(argv,'试运行完成。请查看 apply_audit_dryrun.csv。')
    def apply_commit(self):
        if not self.require(('科目旧新映射 CSV',self.mapping_file),('处理后账套副本目录',self.output_dir)): return
        if not messagebox.askyesno('确认正式处理','请确认原始账套已备份、映射 CSV 已复核、试运行日志无错误。是否继续？'): return
        argv=['kis_multi_year_account_standardizer.py','apply','--mapping',self.mapping_file.get(),'--out',self.output_dir.get(),'--commit']
        if self.config_file.get().strip(): argv+=['--config',self.config_file.get()]
        if self.allow_unconfirmed.get(): argv+=['--allow-unconfirmed']
        self.run_core(argv,'正式处理完成。')
if __name__=='__main__': App().mainloop()
