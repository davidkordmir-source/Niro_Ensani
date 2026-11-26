Target amd64-unicode
!include "MUI2.nsh"
!include "x64.nsh"

Name "Niro Desk"
OutFile "${SETUP}"
InstallDir "$LOCALAPPDATA\Programs\NiroDesk"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Niro Desk"
VIAddVersionKey "LegalCopyright" "Niro Desk contributors"
VIAddVersionKey "FileDescription" "Niro Desk installer"
VIAddVersionKey "FileVersion" "${VERSION}"

!define MUI_ICON "${ICONFILE}"
!define MUI_UNICON "${ICONFILE}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\NiroDesk.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONEXCLAMATION "Niro Desk requires 64-bit Windows."
        Abort
    ${EndIf}
    ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "InstallLocation"
    StrCmp $0 "" same
    StrCpy $INSTDIR $0
same:
FunctionEnd

Section "Niro Desk"
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    File "${LAUNCHER}"
    File "${NOTICE}"
    SetOutPath "$INSTDIR\app"
    File /r "${PAYLOAD}\*.*"
    SetOutPath "$INSTDIR"
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    CreateDirectory "$SMPROGRAMS\Niro Desk"
    CreateShortCut "$DESKTOP\Niro Desk.lnk" "$INSTDIR\NiroDesk.exe" "" "$INSTDIR\NiroDesk.exe" 0
    CreateShortCut "$SMPROGRAMS\Niro Desk\Niro Desk.lnk" "$INSTDIR\NiroDesk.exe" "" "$INSTDIR\NiroDesk.exe" 0
    CreateShortCut "$SMPROGRAMS\Niro Desk\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "DisplayName" "Niro Desk"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "DisplayIcon" "$INSTDIR\NiroDesk.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk" "NoRepair" 1
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    Delete "$DESKTOP\Niro Desk.lnk"
    Delete "$SMPROGRAMS\Niro Desk\Niro Desk.lnk"
    Delete "$SMPROGRAMS\Niro Desk\Uninstall.lnk"
    RMDir "$SMPROGRAMS\Niro Desk"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NiroDesk"
    Delete "$INSTDIR\NiroDesk.exe"
    Delete "$INSTDIR\NOTICE.txt"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir /r "$INSTDIR\app"
    RMDir "$INSTDIR"
SectionEnd
