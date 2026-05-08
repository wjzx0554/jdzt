# 金蝶 KIS 多年账套科目标准化工具

本工具用于处理金蝶 KIS 迷你版/标准版多年老账套在升级、合并或迁移时遇到的科目编码不一致问题。

目标是：快速扫描多个年度账套，生成多年科目汇总和“需要人工确认”的重编码表，然后只对确认过的行修改账套副本。原始账套文件不会被直接修改。

## 核心规则

当前底层判断只保留两条：

1. 多年科目汇总后，只按 `科目编码 + 科目名称` 删除完全重复项。
2. 只有“同一个科目编码对应多个不同科目名称”才标记为需要人工重编码。

特别注意：

- 科目编码是主判断依据。
- 科目名称只用于显示和判断“同编码不同名称”。
- 同名不同编码视为不同科目，不提示、不合并、不自动改码。
- 不按名称推断一级科目，不按名称自动合并，不随意生成新编码。
- 只有同编码不同名称、跨年度父级需下设明细这两类会进入人工确认表。
- `needs_recode` 行会按 `GLPref.FAcLen1..FAcLen6` 的分段级次自动生成 `new_code`，人工只负责复核并确认。
- `FAcLen1..FAcLen6` 是分段长度，例如 `3-2-2`；判断父级时内部临时换算为累计长度 `3,5,7`，写回 `GLPref` 时仍写分段长度。

## 推荐流程

1. 准备账套目录，里面可以包含多个 `.AIS/.AIY/.AXX/.MDB` 文件。
2. 运行快速扫描 `scan-kis`，生成汇总表、人工确认表和辅助报告。
3. 重点查看：
   - `01_多年科目汇总_去重.csv`
   - `02_需要人工确认的科目重编码表.csv`
   - `03_科目级次修改计划.csv`
4. 在 `02_需要人工确认的科目重编码表.csv` 中复核自动生成的新编码和新名称，确认后把 `confirmed` 或 `是否确认` 填为 `Y`。
5. 先运行 dry-run，确认 `preflight_report.csv` 和 `09_账套修改审计.csv` 没有阻断项。
6. 最后再 commit，工具会复制账套副本并只修改副本。
7. commit 成功后，在 `输出目录/处理后账套/` 查看修改后的账套文件。

普通科目不会进入人工确认表，也不会在 apply 时被修改。

## 自动重编码规则

自动生成新编码时只处理两种情况：

1. 同一账套组内，同一个科目编码对应多个不同科目名称。
2. 某年度只有父级科目，而其他年度已经存在该父级下的明细科目，需要为这个年度新增一个专用下级科目。

同编码不同名称时：

- 出现年度最多的科目名称保留原编码；
- 出现次数相同，则最早年度保留原编码；
- 其他名称按年度顺序，在原编码同一父级下寻找下一个可用编码。

跨年度父级需下设明细时：

- 以父级编码为基础生成下级编码；
- 新名称默认使用年度，例如 `2001年`；
- apply 时会先扩展 `GLPref` 科目级次，再在副本科目表中插入下级科目，并把该年度引用迁移到新编码。

级次判断使用 `GLPref.FAcLen1..FAcLen6`：

- `FAcLen1..FAcLen6` 是分段长度，不是累计长度；
- 例如 `FAcLen1=3, FAcLen2=2, FAcLen3=2` 表示 `3-2-2`；
- 判断父级时工具内部临时使用累计长度 `3,5,7`；
- 写回 `GLPref` 时仍写分段长度 `3,2,2`。

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
00_账套科目原始明细.csv
01_多年科目汇总_去重.csv
02_需要人工确认的科目重编码表.csv
03_科目级次修改计划.csv
04_核算项目汇总.csv
05_扫描错误报告.csv
06_扫描性能统计.csv
07_账套修改审计.csv
```

说明：

- `00_账套科目原始明细.csv` 不去重，保留每个账套、年度、文件 ID、原始编码、原始名称。
- `01_多年科目汇总_去重.csv` 只删除 `科目编码 + 科目名称` 完全重复项，按科目编码排序。
- `02_需要人工确认的科目重编码表.csv` 只输出需要人工确认的行；普通 `old_code = new_code` 科目不会输出。
- 人工确认表保留 `source_file_id + year + old_code`，apply 时按账套文件 ID、年度和旧编码定位，不能只按旧编码全局替换。
- `03_科目级次修改计划.csv` 记录每个账套副本是否需要扩展 `GLPref` 科目级次。
- `07_账套修改审计.csv` 是扫描阶段的空审计占位，真正写库审计在 apply 输出目录中生成。

人工确认表的主要列：

```text
账套组
账套文件
账套文件ID
年度
冲突类型
旧科目编码
旧科目名称
自动生成新编码
自动生成新名称
编码规则
是否确认
原因
source_file
source_file_id
year
old_code
old_name
new_code
new_name
action
confirmed
```

只需要审核 `02_需要人工确认的科目重编码表.csv` 中出现的行。确认无误后，把 `confirmed` 或 `是否确认` 填为 `Y`；未确认的行默认跳过。

## 命令行使用

快速扫描：

```powershell
python kis_multi_year_account_standardizer.py scan-kis --input "账套目录" --out "工作目录"
```

试运行：

```powershell
python kis_multi_year_account_standardizer.py apply --mapping "工作目录\02_需要人工确认的科目重编码表.csv" --out "副本输出目录" --dry-run
```

正式写入副本：

```powershell
python kis_multi_year_account_standardizer.py apply --mapping "工作目录\02_需要人工确认的科目重编码表.csv" --out "副本输出目录" --commit
```

`kiszt.py` 也支持同样的命令，同时提供一个简化图形向导：

```powershell
python kiszt.py
```

双击打包后的 `KISZT_x86.exe` / `KISZT_x64.exe` 会打开图形向导。推荐按向导顺序操作：

```text
选择账套目录 -> 扫描账套 -> 审核人工确认表 -> dry-run -> 正式生成副本
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
- 人工确认表中 `confirmed != Y` 的行默认跳过，不写账套；
- 已确认行如果旧科目不存在、缺父级、重复目标编码等会阻止 commit；
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

apply 输出目录会生成：

```text
00_处理结果汇总.csv
preflight_report.csv
reference_fields_report.csv
apply_audit_dryrun.csv
apply_audit_commit.csv
09_账套修改审计.csv
skipped_unconfirmed_mapping.csv
```

- `00_处理结果汇总.csv`：每个账套是否生成副本、是否执行修改、修改后账套完整路径、失败原因。
- `preflight_report.csv`：写库前校验结果。
- `reference_fields_report.csv`：本次允许更新的白名单引用字段。
- `09_账套修改审计.csv`：dry-run 或 commit 的汇总审计。
- `skipped_unconfirmed_mapping.csv`：未确认、被跳过的人工确认行。

正式生成的账套统一放在：

```text
输出目录/处理后账套/
```

文件名格式：

```text
年度_原账套文件名
```

例如：

```text
输出目录/处理后账套/2001_示例账套.Ais
```

如果本次没有任何 `confirmed=Y` 的修改行，或 commit 被 preflight 阻止，工具会明确打印“未生成修改后的账套文件”，并在 `00_处理结果汇总.csv` 写明原因。

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
scan-kis -> 人工审核 02_需要人工确认的科目重编码表.csv -> dry-run -> 查看审计 -> commit
```

不要跳过 dry-run。
