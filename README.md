# 金蝶 KIS 多年账套科目标准化工具

本工具用于处理金蝶 KIS 迷你版/标准版多年老账套在升级云会计多年合并时遇到的“科目被修改”问题。

核心目标：

1. 读取多个年度 `.AIS/.AIY/.AXX/.MDB` 账套中的科目。
2. 汇总所有年度科目，生成统一标准科目体系。
3. 识别同编码多名称、同名称多编码、父级科目被凭证使用等问题。
4. 生成“旧账套 + 年度 + 旧编码 + 旧名称 → 新编码 + 新名称”的映射表。
5. 根据确认后的映射，修改每个年度账套副本里的科目表、凭证引用、余额引用等。
6. 输出可直接上传云会计进行多年合并升级的账套副本。

## 推荐流程

老账套，尤其 2001—2007 年账套，优先使用 x86/32 位版本。

## GitHub Actions 自动打包

本仓库包含：

```text
.github/workflows/build-windows.yml
```

推送代码或手动运行 workflow 后，会自动生成：

```text
KIS_MultiYear_Standardizer_GUI_x86.exe
KIS_MultiYear_Standardizer_CLI_x86.exe
KIS_MultiYear_Standardizer_GUI_x64.exe
KIS_MultiYear_Standardizer_CLI_x64.exe
```

在 Actions 运行完成后的 Artifacts 中下载。

## 运行环境注意

- x86 exe 需要 32 位 Access/Jet/ACE 驱动。
- x64 exe 需要 64 位 Access/ACE 驱动。
- 2001—2007 年老账套优先使用 x86 exe + 32 位驱动。

如果报 IM002，说明当前 exe 位数下看不到 Access ODBC 驱动。

## 安全说明

工具不会覆盖原始账套。正式处理时会在输出目录生成账套副本。

正式处理前必须备份原账套，并先试运行查看审计日志。
