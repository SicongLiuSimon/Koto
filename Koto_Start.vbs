' Koto 静默启动器 v3.0
' 双击此文件可在桌面静默启动 Koto，不显示任何控制台窗口
' 进程名: pythonw.exe (系统托盘不可见，但任务管理器可见)
'
' 如需停止 Koto，请运行 Stop_Koto.bat

Option Explicit

Dim oShell, oFSO, sRoot, sPS1, sCmd

Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
sRoot = oFSO.GetParentFolderName(WScript.ScriptFullName)
sPS1  = sRoot & "\launcher\Koto_Start.ps1"

' 检查 PS1 脚本存在
If Not oFSO.FileExists(sPS1) Then
    MsgBox "错误: 找不到 Koto_Start.ps1" & vbCrLf & _
           "路径: " & sPS1, vbCritical, "Koto 启动失败"
    WScript.Quit 1
End If

' 构建 PowerShell 命令（静默，无窗口）
sCmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden " & _
       "-ExecutionPolicy Bypass -File """ & sPS1 & """ -Mode desktop"

' 0 = 隐藏窗口, False = 不等待完成（启动后立即返回）
oShell.Run sCmd, 0, False

Set oShell = Nothing
Set oFSO   = Nothing

' 启动后立刻退出 VBS，Koto 在后台继续运行
WScript.Quit 0
