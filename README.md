# 金蝶 KIS 多年账套科目标准化工具

本工具用于处理金蝶 KIS 迷你版/标准版多年老账套在升级、合并或迁移时遇到的科目编码不一致问题。

目标是：快速扫描多个年度账套，生成可人工确认的科目映射表，然后只在确认后修改账套副本。原始账套文件不会被直接修改。

## 核心规则

当前底层判断只保留两条：

1. 多年科目汇总后，只按 `科目编码 + 科目名称` 删除完全重复项。
2. 只有“同一个科目编码对应多个不同科目名称”才标记为需要人工重编码。

特别注意：

- 科目编码是主判断依据。
- 科目名称只用于显示和判断“同编码不同名称”。
- 同名不同编码视为不同科目，不提示、不合并、不自动改码。
- 不按名称推断一级科目，不按名称自动合并，不随意生成新编码。
- `needs_recode` 行的 `new_code` 会留空，必须人工确认后填写。

## 推荐流程

1. 准备账套目录，里面可以包含多个 `.AIS/.AIY/.AXX/.MDB` 文件。
2. 运行快速扫描 `scan-kis`，生成 01-09 号 CSV。
3. 重点查看：
   - `02_多年科目汇总_去重.csv`
   - `03_科目编码冲突检查.csv`
   - `04_科目映射确认表.csv`
   - `05_科目级次修改计划.csv`
4. 在 `04_科目映射确认表.csv` 中人工填写需要重编码的 `new_code`，确认后把 `confirmed` 填为 `Y`。
5. 先运行 dry-run，确认 `preflight_report.csv` 和 `09_账套修改审计.csv` 没有阻断项。
6. 最后再 commit，工具会复制账套副本并只修改副本。

## 快速扫描读取范围

`scan-kis` 是只读扫描，设计目标是快，不做全库分析。

扫描阶段只读取：

- `GLPref`：公司、年度、期间、科目级次；
- `GLAcct`：科目编码、科目名称；
- `GLObj` / `GLCls` / `GLEmp` / `PAData` / `PAItem`：存在时用于核算项目汇总；
- `GLVch`：只做 MIN/MAX 聚合判断年度/期间。

扫描阶段不会：

- 不读取余额表；
- 不全量读取凭证；
- 不扫描所有表字段；
- 不调用引用字段扫描；
- 不执行 `UPDATE/INSERT/DELETE`。

## 输出文件

快速扫描统一输出：

```text
01_账套科目原始明细.csv
02_多年科目汇总_去重.csv
03_科目编码冲突检查.csv
04_科目映射确认表.csv
05_科目级次修改计划.csv
06_核算项目汇总.csv
07_扫描错误报告.csv
08_扫描性能统计.csv
09_账套修改审计.csv
```

说明：

- `01_账套科目原始明细.csv` 不去重，保留每个账套、年度、文件 ID、原始编码、原始名称。
- `02_多年科目汇总_去重.csv` 只删除 `科目编码 + 科目名称` 完全重复项。
- `03_科目编码冲突检查.csv` 只检查同编码不同名称等真正需要处理的问题。
- `04_科目映射确认表.csv` 是 apply 的输入文件，必须保留 `source_file_id + year + old_code`。
- `09_账套修改审计.csv` 记录 dry-run 或 commit 的计划和执行情况。

## 命令行使用

快速扫描：

```powershell
python kis_multi_year_account_standardizer.py scan-kis --input "账套目录" --out "工作目录"
```

试运行：

```powershell
python kis_multi_year_account_standardizer.py apply --mapping "工作目录\04_科目映射确认表.csv" --out "副本输出目录" --dry-run
```

正式写入副本：

```powershell
python kis_multi_year_account_standardizer.py apply --mapping "工作目录\04_科目映射确认表.csv" --out "副本输出目录" --commit
```

`kiszt.py` 也支持同样的命令，同时提供一个简化图形向导：

```powershell
python kiszt.py
```

## apply 安全规则

正式写入时：

- 必须复制账套副本，不直接修改原始账套；
- 必须重新读取副本中的 `GLPref` 和 `GLAcct`；
- 必须通过 `source_file_id + year + old_code` 匹配映射；
- 必须先执行 preflight；
- 必须先处理 `GLPref` 科目级次，再处理 `GLAcct`；
- 科目表 `GLAcct` 由独立逻辑处理，不作为普通引用字段更新；
- 业务表只按白名单字段更新；
- 任意写库错误都会 rollback；
- `confirmed != Y`、旧科目不存在、缺父级、重复目标编码等都会阻止 commit；
- 输出目录已有同名副本时默认阻止覆盖。

第一批白名单引用字段包括：

```text
GLVch.FAcctID
GLBal.FAcctID
GLBalHist.FAcctID
GLInitBal.FAcctID
GLInitData.FAcctID
GLPref 中的默认科目字段
```

## 老账套环境

老 `.AIS` 账套通常需要 32 位 Access/Jet 驱动和 `System.mda`。

- 2001-2007 年左右的老账套优先使用 x86/32 位版本。
- x86 exe 需要 32 位 Access/Jet/ACE 驱动。
- x64 exe 需要 64 位 Access/ACE 驱动。
- 发布包会包含 `临时存储文件/System.mda`，程序会自动尝试使用。
- 换电脑使用时，请保持 exe 与 `临时存储文件/System.mda` 在同一个发布包目录结构中。

如果报 IM002，说明当前 exe 位数下看不到 Access ODBC 驱动。

## GitHub Actions 自动打包

本仓库包含：

```text
.github/workflows/build-windows.yml
```

推送到 `main/master` 或手动运行 workflow 后，会生成 x86 和 x64 发布包。每个发布包包含：

```text
KIS_MultiYear_Standardizer_GUI_x86/x64.exe
KIS_MultiYear_Standardizer_CLI_x86/x64.exe
KISZT_x86/x64.exe
config.template.json
mapping_template.csv
README.md
Access_ODBC_IM002修复说明.md
临时存储文件/System.mda
```

推荐普通使用者优先打开 `KISZT_x86.exe`，按图形向导一步一步处理。

## 安全提醒

正式处理前请先备份原始账套。

建议顺序永远是：

```text
scan-kis -> 人工审核 04_科目映射确认表.csv -> dry-run -> 查看审计 -> commit
```

不要跳过 dry-run。
