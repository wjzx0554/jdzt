# Access ODBC 驱动错误 IM002 修复说明

如果程序报：

```text
IM002 [Microsoft][ODBC 驱动程序管理器] 未发现数据源名称并且未指定默认驱动程序
```

说明当前 exe 位数下看不到 Access ODBC 驱动。

## 位数对应关系

- x86 exe 需要 32 位 Access/Jet/ACE 驱动。
- x64 exe 需要 64 位 Access/ACE 驱动。

2001—2007 年老账套建议优先使用：

```text
KIS_MultiYear_Standardizer_GUI_x86.exe
```

并在运行电脑上安装 32 位 Access/Jet/ACE 驱动。

## 金蝶 System.mda

很多 KIS 迷你版/标准版 `.AIS` 需要金蝶工作组文件 `System.mda` 才能打开。发布包会把该文件放在 exe 同目录的 `临时存储文件/System.mda` 下，程序会自动使用；如果使用自己的金蝶环境，也可以在 `config.json` 中设置完整路径。

如果不想复制文件，也可以在 `config.json` 中设置：

```json
{
  "access_systemdb": "E:/金蝶AIS账套数据引出工具V1.0/临时存储文件/System.mda",
  "access_uid": "morningstar",
  "access_pwd": "ypbwkfyjhyhgzj"
}
```

## 注意

Access/Jet/ACE 驱动不能简单打包进 exe，通常必须安装到系统中，因为它依赖注册表、ODBC 驱动管理器和 COM/OLEDB 组件。

如果客户电脑装了 64 位 Office，安装 32 位 Access 运行库可能冲突。最稳妥方式是准备一台专门处理老账套的环境：32 位驱动 + x86 工具。
