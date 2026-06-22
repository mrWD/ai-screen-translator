' Launch AI Screen Translator with NO console window (for everyday use).
' The app still writes its log to %APPDATA%\ai-screen-translator\app.log
' Run setup.bat (or run.bat) once first so the venv exists.
Option Explicit
Dim fso, sh, base, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Dim venv
venv = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\ai-screen-translator\venv"
pyw = venv & "\Scripts\pythonw.exe"
' Require the setup-complete marker, not just pythonw.exe: venv creation precedes
' the ~1GB pip install, so a half-built env has pythonw but no working imports.
If (Not fso.FileExists(pyw)) Or (Not fso.FileExists(venv & "\.setup_ok")) Then
  MsgBox "Not set up yet (or setup didn't finish). Please double-click setup.bat (or run.bat) once first.", _
         vbExclamation, "AI Screen Translator"
  WScript.Quit 1
End If
' Run from the project folder so the screen_translator package is importable.
sh.CurrentDirectory = base
' window style 0 = hidden, False = don't wait
sh.Run """" & pyw & """ -m screen_translator", 0, False
