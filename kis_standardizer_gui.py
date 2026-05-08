# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import csv
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import kis_multi_year_account_standardizer as core

APP_TITLE = '金蝶多年账套科目标准化工具'
PREVIEW_LIMIT = 300


class TextRedirector:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1180x780')
        self.minsize(1060, 680)
        self.log_queue = queue.Queue()
        self.input_dir = tk.StringVar()
        self.work_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.config_file = tk.StringVar()
        self.mapping_file = tk.StringVar()
        self.export_input_dir = tk.StringVar()
        self.export_mapping_file = tk.StringVar()
        self.export_output_dir = tk.StringVar()
        self.allow_unconfirmed = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value='请选择原始账套目录，然后执行快速扫描。')
        self.buttons = []
        self.trees = {}
        self.tab_labels = {}
        self.busy = False
        self.build()
        self.after(100, self.poll)

    def build(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text=APP_TITLE, font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(
            root,
            text='按步骤处理老 AIS 账套：先快速扫描关键 KIS 表，在软件里查看科目汇总和人工确认表，确认后再试运行和生成副本。正式副本会放在输出目录的“处理后账套”子目录。',
            wraplength=1100,
        ).pack(anchor='w', pady=(4, 12))

        paths = ttk.LabelFrame(root, text='路径设置', padding=10)
        paths.pack(fill=tk.X)
        self.row(paths, 0, '原始账套目录', self.input_dir, 'dir')
        self.row(paths, 1, '扫描/映射输出目录', self.work_dir, 'dir')
        self.row(paths, 2, '处理结果输出目录', self.output_dir, 'dir')
        self.row(paths, 3, '配置文件 config.json', self.config_file, 'save')
        self.row(paths, 4, '科目映射 CSV', self.mapping_file, 'file')
        ttk.Label(
            paths,
            textvariable=self.status_text,
            foreground='#444',
        ).grid(row=5, column=0, columnspan=3, sticky='w', pady=(8, 0))

        steps = ttk.LabelFrame(root, text='一步一步操作', padding=10)
        steps.pack(fill=tk.X, pady=12)
        self.add_button(steps, '1. 生成配置', self.make_config).pack(side=tk.LEFT, padx=(0, 8))
        self.add_button(steps, '2. 快速扫描 KIS', self.scan_kis).pack(side=tk.LEFT, padx=(0, 8))
        self.add_button(steps, '3. 刷新下方结果', self.refresh_previews).pack(side=tk.LEFT, padx=(0, 8))
        self.add_button(steps, '4. 打开映射 CSV', self.open_mapping).pack(side=tk.LEFT, padx=(0, 8))
        self.add_button(steps, '5. 试运行检查', self.apply_dry).pack(side=tk.LEFT, padx=(0, 8))
        self.add_button(steps, '6. 正式生成副本', self.apply_commit).pack(side=tk.LEFT, padx=(0, 8))

        tools = ttk.Frame(root)
        tools.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(tools, text='允许处理未确认映射，不建议勾选', variable=self.allow_unconfirmed).pack(side=tk.LEFT)
        self.add_button(tools, '打开扫描目录', self.open_work_dir).pack(side=tk.RIGHT, padx=(8, 0))
        self.add_button(tools, '打开处理结果目录', self.open_output_dir).pack(side=tk.RIGHT, padx=(8, 0))
        self.add_button(tools, '旧版全量分析', self.inspect).pack(side=tk.RIGHT, padx=(8, 0))

        export_box = ttk.LabelFrame(root, text='老账套凭证汇总导出', padding=10)
        export_box.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(
            export_box,
            text='适用于太老的 AIS 账套：不修改账套文件，只按科目映射导出凭证汇总，供后续人工导入。',
            wraplength=1080,
        ).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        self.row(export_box, 1, '老账套目录', self.export_input_dir, 'dir')
        self.row(export_box, 2, '科目映射确认表', self.export_mapping_file, 'file')
        self.row(export_box, 3, '导出输出目录', self.export_output_dir, 'dir')
        self.add_button(export_box, '导出凭证汇总', self.export_vouchers).grid(row=4, column=2, sticky='e', pady=(8, 0))

        body = ttk.PanedWindow(root, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True)

        preview_box = ttk.LabelFrame(body, text='结果预览', padding=8)
        body.add(preview_box, weight=3)
        self.notebook = ttk.Notebook(preview_box)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        for title in [
            '科目汇总',
            '人工确认',
            '问题汇总',
            '标准科目',
            '核算项目',
            '扫描错误',
            '扫描性能',
            '预检报告',
            '引用字段',
            '处理结果',
            '试运行审计',
            '正式审计',
            '修改科目清单',
            '凭证汇总',
            '导出检查',
            '导出错误',
        ]:
            self.add_preview_tab(title)

        log_box = ttk.LabelFrame(body, text='运行日志', padding=8)
        body.add(log_box, weight=1)
        self.log = tk.Text(log_box, height=10, wrap='word')
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(log_box, command=self.log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.config(yscrollcommand=sb.set)
        self.out('准备就绪。正式处理前请备份原始账套，并先执行试运行检查。\n\n')

    def row(self, parent, row, label, var, kind):
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky='w', pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky='ew', padx=8, pady=4)
        parent.columnconfigure(1, weight=1)
        ttk.Button(parent, text='选择', command=lambda: self.choose(var, kind)).grid(row=row, column=2, pady=4)

    def add_button(self, parent, text, command):
        btn = ttk.Button(parent, text=text, command=command)
        self.buttons.append(btn)
        return btn

    def add_preview_tab(self, title):
        frame = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(frame, text=title)
        label = ttk.Label(frame, text='等待生成文件。')
        label.pack(anchor='w')
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        tree = ttk.Treeview(table_frame, show='headings')
        ysb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        xsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        ysb.grid(row=0, column=1, sticky='ns')
        xsb.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.trees[title] = tree
        self.tab_labels[title] = label

    def choose(self, var, kind):
        if kind == 'dir':
            p = filedialog.askdirectory()
        elif kind == 'file':
            p = filedialog.askopenfilename(filetypes=[('CSV 文件', '*.csv'), ('所有文件', '*.*')])
        else:
            p = filedialog.asksaveasfilename(
                defaultextension='.json',
                filetypes=[('JSON 文件', '*.json'), ('所有文件', '*.*')],
                initialfile='config.json',
            )
        if p:
            var.set(p)

    def out(self, s):
        self.log.insert(tk.END, s)
        self.log.see(tk.END)

    def poll(self):
        try:
            while True:
                self.out(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def require(self, *items):
        miss = [n for n, v in items if not v.get().strip()]
        if miss:
            messagebox.showwarning('缺少路径', '请先填写：' + '、'.join(miss))
            return False
        return True

    def set_busy(self, busy):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in self.buttons:
            btn.configure(state=state)

    def run_core(self, argv, done='完成。', on_success=None, on_failure=None):
        if self.busy:
            messagebox.showinfo('正在运行', '当前任务还没有结束，请稍等。')
            return

        def task():
            old = sys.argv[:]
            result = {'ok': True, 'message': ''}
            self.log_queue.put('\n>>> ' + ' '.join(argv) + '\n')
            try:
                sys.argv = argv[:]
                r = TextRedirector(self.log_queue)
                with contextlib.redirect_stdout(r), contextlib.redirect_stderr(r):
                    core.main()
            except SystemExit as e:
                if e.code not in (0, None):
                    result['ok'] = False
                    result['message'] = '运行结束，返回码：%s' % e.code
                    self.log_queue.put(result['message'] + '\n')
            except Exception:
                result['ok'] = False
                result['message'] = traceback.format_exc()
                self.log_queue.put('\n发生错误：\n' + result['message'])
            finally:
                sys.argv = old

            def finish():
                self.set_busy(False)
                if result['ok']:
                    self.log_queue.put(done + '\n')
                    if on_success:
                        on_success()
                elif on_failure:
                    on_failure(result.get('message') or '运行失败。')

            self.after(0, finish)

        self.set_busy(True)
        threading.Thread(target=task, daemon=True).start()

    def make_config(self):
        if not self.config_file.get().strip():
            self.config_file.set(str(Path.cwd() / 'config.json'))
        self.run_core(
            ['kis_multi_year_account_standardizer.py', 'make-config', '--out', self.config_file.get()],
            '配置文件已生成。',
        )

    def scan_kis(self):
        if not self.require(('原始账套目录', self.input_dir), ('扫描/映射输出目录', self.work_dir)):
            return
        argv = [
            'kis_multi_year_account_standardizer.py',
            'scan-kis',
            '--input',
            self.input_dir.get(),
            '--out',
            self.work_dir.get(),
        ]
        if self.config_file.get().strip():
            argv += ['--config', self.config_file.get()]

        def done():
            mapping = Path(self.work_dir.get()) / '02_需要人工确认的科目重编码表.csv'
            self.mapping_file.set(str(mapping))
            self.status_text.set('快速扫描完成。请先查看“人工确认”和“扫描错误”，必要时打开确认 CSV 复核自动编码并修改 confirmed 列。')
            self.refresh_previews()

        self.run_core(argv, '快速扫描完成。', done)

    def inspect(self):
        if not self.require(('原始账套目录', self.input_dir), ('扫描/映射输出目录', self.work_dir)):
            return
        if not messagebox.askyesno('旧版全量分析', '旧版 inspect 会扫描更多表和字段，可能比较慢。是否继续？'):
            return
        argv = [
            'kis_multi_year_account_standardizer.py',
            'inspect',
            '--input',
            self.input_dir.get(),
            '--out',
            self.work_dir.get(),
        ]
        if self.config_file.get().strip():
            argv += ['--config', self.config_file.get()]

        def done():
            legacy = Path(self.work_dir.get()) / '04_科目旧新映射_草稿.csv'
            self.mapping_file.set(str(legacy))
            self.refresh_previews()

        self.run_core(argv, '旧版全量分析完成。', done)

    def apply_dry(self):
        if not self.require(('科目映射 CSV', self.mapping_file), ('处理后副本目录', self.output_dir)):
            return
        argv = [
            'kis_multi_year_account_standardizer.py',
            'apply',
            '--mapping',
            self.mapping_file.get(),
            '--out',
            self.output_dir.get(),
        ]
        if self.config_file.get().strip():
            argv += ['--config', self.config_file.get()]
        if self.allow_unconfirmed.get():
            argv += ['--allow-unconfirmed']

        def done():
            self.status_text.set('试运行完成。请查看“预检报告”“引用字段”“处理结果”“试运行审计”，没有阻断项再正式生成副本。')
            self.refresh_previews()

        self.run_core(argv, '试运行完成。', done)

    def apply_commit(self):
        if not self.require(('科目映射 CSV', self.mapping_file), ('处理后副本目录', self.output_dir)):
            return
        if not messagebox.askyesno(
            '确认正式处理',
            '请确认原始账套已备份、映射 CSV 已复核、试运行报告无阻断项。是否继续生成账套副本？',
        ):
            return
        argv = [
            'kis_multi_year_account_standardizer.py',
            'apply',
            '--mapping',
            self.mapping_file.get(),
            '--out',
            self.output_dir.get(),
            '--commit',
        ]
        if self.config_file.get().strip():
            argv += ['--config', self.config_file.get()]
        if self.allow_unconfirmed.get():
            argv += ['--allow-unconfirmed']

        def done():
            self.status_text.set('正式处理完成。请查看“处理结果”“正式审计”，账套副本在输出目录的“处理后账套”子目录。')
            self.refresh_previews()

        self.run_core(argv, '正式处理完成。', done)

    def validate_export_mapping(self, path):
        try:
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                fields = reader.fieldnames or []
                if 'confirmed' not in fields and '是否确认' not in fields:
                    messagebox.showwarning('映射表缺少确认列', '映射表必须包含 confirmed 或 是否确认 列。')
                    return False
                confirmed_changes = 0
                for row in reader:
                    confirmed = (row.get('confirmed') or row.get('是否确认') or '').strip().upper() == 'Y'
                    old = (row.get('old_code') or row.get('旧科目编码') or row.get('原始科目编码') or '').strip()
                    new = (row.get('new_code') or row.get('新科目编码') or row.get('自动生成新编码') or '').strip()
                    if confirmed and old and new and old != new:
                        confirmed_changes += 1
                if confirmed_changes == 0:
                    messagebox.showinfo('没有确认的科目修改', '没有确认的科目修改，将按原科目编码汇总凭证。')
                return True
        except Exception as e:
            messagebox.showerror('读取映射表失败', str(e))
            return False

    def export_vouchers(self):
        if not self.require(('老账套目录', self.export_input_dir), ('科目映射确认表', self.export_mapping_file), ('导出输出目录', self.export_output_dir)):
            return
        mapping = Path(self.export_mapping_file.get())
        if not mapping.exists():
            messagebox.showwarning('映射表不存在', str(mapping))
            return
        if not self.validate_export_mapping(mapping):
            return
        argv = [
            'kis_multi_year_account_standardizer.py',
            'export-vouchers',
            '--input',
            self.export_input_dir.get(),
            '--mapping',
            self.export_mapping_file.get(),
            '--out',
            self.export_output_dir.get(),
        ]
        if self.config_file.get().strip():
            argv += ['--config', self.config_file.get()]

        def done():
            out = Path(self.export_output_dir.get())
            self.status_text.set('老账套凭证汇总导出完成。请查看“修改科目清单”“凭证汇总”“导出检查”。')
            self.refresh_previews()
            messagebox.showinfo(
                '导出完成',
                '导出完成\n输出目录路径：%s\n修改科目清单路径：%s\n凭证汇总路径：%s\n检查报告路径：%s'
                % (
                    out,
                    out / '01_修改科目清单.csv',
                    out / '02_凭证汇总_按新科目.csv',
                    out / '03_导出检查报告.csv',
                ),
            )

        def failed(reason):
            out = Path(self.export_output_dir.get())
            messagebox.showerror('导出失败', '导出失败：%s\n请查看错误报告：%s' % (reason, out / '04_导出错误报告.csv'))

        self.run_core(argv, '凭证汇总导出完成。', done, failed)

    def preview_path(self, title):
        work = Path(self.work_dir.get()) if self.work_dir.get().strip() else None
        out = Path(self.output_dir.get()) if self.output_dir.get().strip() else None
        mapping = Path(self.mapping_file.get()) if self.mapping_file.get().strip() else None
        files = {
            '科目汇总': work / '01_多年科目汇总_去重.csv' if work else None,
            '人工确认': mapping if mapping else (work / '02_需要人工确认的科目重编码表.csv' if work else None),
            '标准科目': work / '01_多年科目汇总_去重.csv' if work else None,
            '核算项目': work / '04_核算项目汇总.csv' if work else None,
            '扫描错误': work / '05_扫描错误报告.csv' if work else None,
            '扫描性能': work / '06_扫描性能统计.csv' if work else None,
            '预检报告': out / 'preflight_report.csv' if out else None,
            '引用字段': out / 'reference_fields_report.csv' if out else None,
            '处理结果': out / '00_处理结果汇总.csv' if out else None,
            '试运行审计': out / '09_账套修改审计.csv' if out else None,
            '正式审计': out / '09_账套修改审计.csv' if out else None,
            '修改科目清单': Path(self.export_output_dir.get()) / '01_修改科目清单.csv' if self.export_output_dir.get().strip() else None,
            '凭证汇总': Path(self.export_output_dir.get()) / '02_凭证汇总_按新科目.csv' if self.export_output_dir.get().strip() else None,
            '导出检查': Path(self.export_output_dir.get()) / '03_导出检查报告.csv' if self.export_output_dir.get().strip() else None,
            '导出错误': Path(self.export_output_dir.get()) / '04_导出错误报告.csv' if self.export_output_dir.get().strip() else None,
        }
        return files.get(title)

    def refresh_previews(self):
        for title in self.trees:
            if title == '问题汇总':
                self.load_issue_summary()
            else:
                self.load_preview(title)

    def read_csv_rows(self, path):
        if not path or not path.exists():
            return []
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))

    def load_issue_summary(self):
        title = '问题汇总'
        tree = self.trees[title]
        label = self.tab_labels[title]
        tree.delete(*tree.get_children())
        columns = ['来源', '账套组', '账套文件', '级别', '检查项', '旧科目', '新科目', '原因']
        tree['columns'] = columns
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=150, minwidth=80, stretch=True)
        rows = []
        work = Path(self.work_dir.get()) if self.work_dir.get().strip() else None
        out = Path(self.output_dir.get()) if self.output_dir.get().strip() else None
        sources = [
            ('扫描错误', work / '05_扫描错误报告.csv' if work else None),
            ('预检报告', out / 'preflight_report.csv' if out else None),
            ('引用字段', out / 'reference_fields_report.csv' if out else None),
            ('账套修改审计', out / '09_账套修改审计.csv' if out else None),
        ]
        for source, path in sources:
            for row in self.read_csv_rows(path):
                risk = (row.get('risk_level') or row.get('级别') or '').lower()
                blocked = (row.get('blocked_commit') or '').upper() == 'Y'
                has_error = row.get('error') or row.get('错误')
                if source == '扫描错误' or blocked or risk in ('error', 'high', 'warning') or has_error:
                    rows.append({
                        '来源': source,
                        '账套组': row.get('ledger_group', ''),
                        '账套文件': row.get('source_file') or row.get('ledger_file') or row.get('file') or '',
                        '级别': row.get('risk_level') or ('error' if has_error else ''),
                        '检查项': row.get('check') or row.get('action') or row.get('stage') or '',
                        '旧科目': row.get('old_code', ''),
                        '新科目': row.get('new_code', ''),
                        '原因': row.get('reason') or row.get('error') or row.get('错误') or '',
                    })
        if not rows:
            rows.append({'来源': '汇总', '级别': 'info', '检查项': 'ok', '原因': '暂未发现扫描错误或阻断项。'})
        for row in rows[:PREVIEW_LIMIT]:
            tree.insert('', tk.END, values=[row.get(col, '') for col in columns])
        label.configure(text='显示前 %s 行，共 %s 条问题或提示。' % (min(len(rows), PREVIEW_LIMIT), len(rows)))

    def load_preview(self, title):
        path = self.preview_path(title)
        tree = self.trees[title]
        label = self.tab_labels[title]
        tree.delete(*tree.get_children())
        tree['columns'] = []
        if not path or not path.exists():
            label.configure(text='文件尚未生成。')
            return
        try:
            shown = []
            total = 0
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                columns = reader.fieldnames or []
                for row in reader:
                    total += 1
                    if len(shown) < PREVIEW_LIMIT:
                        shown.append(row)
            tree['columns'] = columns
            for col in columns:
                tree.heading(col, text=col)
                tree.column(col, width=140, minwidth=80, stretch=True)
            for row in shown:
                tree.insert('', tk.END, values=[row.get(col, '') for col in columns])
            label.configure(text='%s，显示前 %s 行，共 %s 行。' % (path, len(shown), total))
        except Exception as e:
            label.configure(text='读取失败：%s' % e)

    def open_mapping(self):
        if not self.mapping_file.get().strip():
            candidate = Path(self.work_dir.get()) / '02_需要人工确认的科目重编码表.csv' if self.work_dir.get().strip() else None
            if candidate and candidate.exists():
                self.mapping_file.set(str(candidate))
        self.open_path(self.mapping_file.get())

    def open_work_dir(self):
        self.open_path(self.work_dir.get())

    def open_output_dir(self):
        self.open_path(self.output_dir.get())

    def open_path(self, value):
        if not value:
            messagebox.showwarning('路径为空', '请先选择或生成对应路径。')
            return
        path = Path(value)
        if not path.exists():
            messagebox.showwarning('路径不存在', str(path))
            return
        os.startfile(str(path))


if __name__ == '__main__':
    App().mainloop()
