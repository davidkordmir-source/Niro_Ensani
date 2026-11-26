Target amd64-unicode
Name "Niro Desk"
OutFile "${LAUNCHER}"
Icon "${ICONFILE}"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
VIProductVersion "${VERSION}.0"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductName" "Niro Desk"
VIAddVersionKey "LegalCopyright" "Niro Desk contributors"
VIAddVersionKey "FileDescription" "Niro Desk personnel workspace"

Section
    SetOutPath "$EXEDIR\app"
    IfFileExists "$EXEDIR\app\pythonw.exe" 0 missing
    Exec '"$EXEDIR\app\pythonw.exe" -I -B "$EXEDIR\app\launch.py"'
    IfErrors missing done
missing:
    MessageBox MB_ICONEXCLAMATION "Niro Desk could not start. Reinstall Niro Desk from its setup file."
done:
SectionEnd
