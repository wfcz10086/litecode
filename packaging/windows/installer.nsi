; LiteCode 桌面版完整离线安装包
; 内含: LiteCode.exe (托盘/安装逻辑) + payload\litecode-rootfs-<版本>.tar.gz (完整运行环境, WSL 系统包)
; 装到 %LOCALAPPDATA%\LiteCode, 普通用户权限即可 (只有从没开过 WSL 的电脑才会再请求一次管理员权限)。
; 构建: makensis -DVER=2.0.0 -DSRC=<含 LiteCode.exe 与 rootfs 的目录> installer.nsi
Unicode true
!include "MUI2.nsh"

!ifndef VER
  !error "需要 -DVER=版本号"
!endif
!ifndef SRC
  !error "需要 -DSRC=文件目录"
!endif

Name "LiteCode ${VER}"
OutFile "${SRC}\LiteCode-Setup-${VER}.exe"
InstallDir "$LOCALAPPDATA\LiteCode"
RequestExecutionLevel user
SetCompressor lzma
BrandingText "LiteCode ${VER}"
VIProductVersion "${VER4}"   ; NSIS 要求四段, release.sh 补齐 (2.0.0 → 2.0.0.0)
VIAddVersionKey "ProductName" "LiteCode"
VIAddVersionKey "FileDescription" "LiteCode 桌面版安装程序"
VIAddVersionKey "ProductVersion" "${VER}"
VIAddVersionKey "FileVersion" "${VER4}"

!define MUI_ICON "assets\litecode.ico"
!define MUI_UNICON "assets\litecode.ico"
!define MUI_WELCOMEPAGE_TITLE "安装 LiteCode ${VER}"
!define MUI_WELCOMEPAGE_TEXT "LiteCode 会以独立的 WSL 子系统运行在本机，只对本机开放（127.0.0.1）。$\r$\n$\r$\n安装包已包含全部组件（含 WSL），安装过程不需要联网。$\r$\n$\r$\n默认登录密码 admin，请登录后在「设置 → 修改密码」改掉。使用说明在开始菜单「LiteCode 使用说明」。$\r$\n$\r$\n为了让 LiteCode 能操控本机（截图、点击、执行命令），配置最后会请求一次管理员权限，安装 OpenSSH Server（开机自启，仅本机/局域网、仅密钥登录）和桌面代理；不需要可在弹窗里点「否」，以后可在托盘菜单补装。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\LiteCode.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--setup"
!define MUI_FINISHPAGE_RUN_TEXT "继续配置并启动 LiteCode"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\LiteCode"

Section "LiteCode"
  ; 升级时旧托盘还开着会锁住 LiteCode.exe
  nsExec::Exec 'taskkill /F /IM LiteCode.exe'
  SetOutPath "$INSTDIR"
  File "${SRC}\LiteCode.exe"
  SetOutPath "$INSTDIR\payload"
  Delete "$INSTDIR\payload\*.tar.gz"
  SetCompress off
  File "${SRC}\litecode-rootfs-${ROOTFS_VER}.tar.gz"   ; 文件名用系统包内部版本 (依赖层缓存时 ≠ 发布版本)
  File "${SRC}\litecode-code-${VER}.tar.gz"           ; 代码更新包: 系统包没变时安装只换代码
  File "${SRC}\wsl.msi"
  SetCompress auto
  SetOutPath "$INSTDIR"
  ; 解压完立即放桌面/开始菜单图标 (原来要等配置、启动全部成功才创建, 中途卡住就一直没图标);
  ; 没配置完时双击图标 = 接着安装
  CreateShortCut "$DESKTOP\LiteCode.lnk" "$INSTDIR\LiteCode.exe" "" "$INSTDIR\LiteCode.exe" 0
  CreateShortCut "$SMPROGRAMS\LiteCode.lnk" "$INSTDIR\LiteCode.exe" "" "$INSTDIR\LiteCode.exe" 0
  File "assets\guide.html"
  CreateShortCut "$SMPROGRAMS\LiteCode 使用说明.lnk" "$INSTDIR\guide.html"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "LiteCode"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VER}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "LiteCode"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\LiteCode.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM LiteCode.exe'
  ; 移除本机桌面代理 (操控本机 this-pc, 要管理员确认; 静默卸载时跳过)。OpenSSH Server 保留
  IfSilent +2
  ExecWait '"$INSTDIR\LiteCode.exe" --remove-local-control'
  nsExec::Exec 'wsl.exe --terminate LiteCode'
  nsExec::Exec 'wsl.exe --unregister LiteCode'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "LiteCode"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "LiteCodeSetup"
  Delete "$DESKTOP\LiteCode.lnk"
  Delete "$SMPROGRAMS\LiteCode.lnk"
  Delete "$SMPROGRAMS\LiteCode 使用说明.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
